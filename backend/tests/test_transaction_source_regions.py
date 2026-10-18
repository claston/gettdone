from dataclasses import asdict
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.parsers.pdf.models import PdfParseResult
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.source_regions import SourceRegion, parse_source_regions
from app.application.transaction_source_regions import bind_transaction_source_regions
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def parsed(pages, anchors, *, extraction=None):
    return PdfParseResult(
        transactions=[NormalizedTransaction("2026-04-01", "SAME", 10, "inflow") for _ in anchors],
        canonical_transactions=[CanonicalTransaction("2026-04-01", "SAME", 10, "inflow", source_page=page, source_line=line)
                                for page, line in anchors],
        layout=PdfLayoutInference("generic_statement_ptbr", 0.25, True),
        extracted_text="\n".join(pages), source_page_texts=tuple(pages),
        source_extraction=extraction,
        parse_metrics={"extraction_provider": "aws_textract" if extraction else "native_pdf_text"},
    )


def test_native_repeated_identical_lines_are_bound_by_source_position_not_description():
    lines = ("HEADER", "01/04/2026 SAME 10,00", "01/04/2026 SAME 10,00")
    result = parsed(["\n".join(lines)], [(1, 2), (1, 3)])
    enriched = bind_transaction_source_regions(result, generate_native_text_pdf((lines,)))
    first, second = [row.source_regions[0] for row in enriched.canonical_transactions]
    assert first.page == second.page == 1
    assert first.top < second.top
    assert first.left == pytest.approx(48 / 595, abs=0.005)
    assert first.width > 0 and first.height > 0
    assert enriched.transactions == result.transactions
    assert result.canonical_transactions[0].source_regions == []


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_regions_use_the_visible_crop_and_intrinsic_rotation(rotation):
    raw = generate_native_text_pdf((("ANCHOR",),))
    writer = PdfWriter()
    page = writer.add_page(PdfReader(BytesIO(raw)).pages[0])
    page.cropbox.lower_left, page.cropbox.upper_right = (20, 30), (550, 820)
    page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    enriched = bind_transaction_source_regions(parsed(["ANCHOR"], [(1, 1)]), output.getvalue())
    region = enriched.canonical_transactions[0].source_regions[0]
    expected = {0: (0.054, 0.028), 90: (0.962, 0.054), 180: (0.85, 0.962), 270: (0.028, 0.85)}
    left, top = expected[rotation]
    assert region.left == pytest.approx(left, abs=0.025)
    assert region.top == pytest.approx(top, abs=0.025)


def test_native_mismatch_missing_page_and_unreadable_pdf_do_not_invent_regions():
    raw = generate_native_text_pdf((("UNRELATED",),))
    for pages, anchors, content in [(["OTHER"], [(1, 1)], raw), (["UNRELATED"], [(2, 1)], raw),
                                     (["UNRELATED"], [(1, 1)], b"invalid"),
                                     (["", "UNRELATED"], [(2, 1)], raw)]:
        result = bind_transaction_source_regions(parsed(pages, anchors), content)
        assert result.canonical_transactions[0].source_regions == []


def test_native_ambiguous_duplicate_lines_in_a_different_stream_are_not_guessed():
    lines = ("HEADER", "SAME", "SAME", "FOOTER")
    result = parsed(["HEADER\nSAME\nSAME"], [(1, 2), (1, 3)])
    enriched = bind_transaction_source_regions(result, generate_native_text_pdf((lines,)))
    assert all(row.source_regions == [] for row in enriched.canonical_transactions)


def test_multiline_regions_follow_only_lines_actually_consumed_by_the_assembler():
    from dataclasses import replace

    from app.application.normalization.canonical import build_canonical_transactions
    from app.application.parsers.pdf.models import _PdfLine
    from app.application.parsers.pdf.multiline_transaction_assembler import parse_multiline_transaction_rows

    lines = ("01/04/2026", "PIX RECEBIDO", "CLIENTE SINTETICO", "100,00 C", "200,00")
    rows, _ = parse_multiline_transaction_rows([_PdfLine(line, 1, index) for index, line in enumerate(lines, 1)])
    assert len(rows) == 1
    canonical = build_canonical_transactions(rows, bank_name=None, layout_name="generic_statement_ptbr",
        layout_used_fallback=True, layout_confidence=0.25, source_parser="multiline")
    result = replace(parsed(["\n".join(lines)], [(1, 1)]), canonical_transactions=canonical)
    regions = bind_transaction_source_regions(result, generate_native_text_pdf((lines,))).canonical_transactions[0].source_regions
    assert len(regions) >= 4
    assert [region.top for region in regions] == sorted(region.top for region in regions)


def test_pipeline_storage_and_response_keep_regions_immutable_after_edits(tmp_path, monkeypatch):
    from app.application.conversion.result_payload import build_analyze_response, persist_conversion_result
    from app.application.conversion.uploaded_document import ingest_uploaded_document
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.storage_service import TempAnalysisStorage
    from tests.test_wise_statements import wise_lines

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((tuple(wise_lines("BRL", balance="1,50")),))
    document = ingest_uploaded_document("synthetic.pdf", raw)
    pipeline = build_default_conversion_pipeline()
    result = pipeline.run_document(document=document, analysis_id="an_regions")
    storage = TempAnalysisStorage(root_dir=tmp_path)
    persisted = persist_conversion_result(storage=storage, pipeline_result=result)
    payload = build_analyze_response(persisted_result=persisted).model_dump()
    original = payload["preview_transactions"][0]
    assert original["source_regions"]
    identifier = original["transaction_id"]
    edited = storage.apply_convert_edits("an_regions", [{"row_id": identifier,
        "date": "2026-04-02", "description": "EDITED ENTIRELY", "credit": 55}])
    changed = next(row for row in edited["preview_transactions"] if row["transaction_id"] == identifier)
    assert changed["source_regions"] == original["source_regions"]
    deleted = storage.apply_convert_edits("an_regions", [{"row_id": identifier, "action": "delete"}])
    assert next(row for row in deleted["preview_transactions"] if row["transaction_id"] == identifier)["source_regions"]
    restored = storage.apply_convert_edits("an_regions", [{"row_id": identifier, "action": "restore",
        "date": "2026-04-03", "description": "RESTORED", "credit": 55}])
    restored_row = next(row for row in restored["preview_transactions"] if row["transaction_id"] == identifier)
    assert restored_row["source_regions"] == original["source_regions"]
    inserted = storage.apply_convert_edits("an_regions", [{"action": "insert", "transaction_id": "manual-region-test",
        "date": "2026-04-03", "description": "MANUAL", "credit": 1}])
    assert next(row for row in inserted["preview_transactions"] if row["transaction_id"] == "manual-region-test")["source_regions"] == []


def test_legacy_storage_without_regions_can_still_be_edited(tmp_path):
    import json

    from app.application.models import AnalysisData, TransactionRow
    from app.application.storage_service import TempAnalysisStorage

    storage = TempAnalysisStorage(root_dir=tmp_path)
    row = TransactionRow("2026-04-01", "OLD", 10, "Outros", "unmatched", source_page=2)
    storage.save_analysis(AnalysisData("an_legacy_regions", "pdf", "old.pdf", 1, 10, 0, 10, [row], [row]))
    snapshot_path = tmp_path / "an_legacy_regions" / "analysis.json"
    old = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for name in ("preview_transactions", "report_transactions"):
        for item in old[name]:
            item.pop("source_regions", None)
    snapshot_path.write_text(json.dumps(old), encoding="utf-8")
    snapshot = storage.apply_convert_edits("an_legacy_regions", [{"row_id": "row_1", "date": "2026-04-01",
        "description": "STILL REVIEWABLE", "credit": 10}])
    assert snapshot["preview_transactions"][0]["source_regions"] == []
    assert snapshot["preview_transactions"][0]["source_page"] == 2


def test_ocr_regions_resolve_actual_line_boxes_without_another_ocr():
    box = {"left": 0.1, "top": 0.3, "width": 0.7, "height": 0.02}
    extraction = RawDocumentExtraction("aws_textract", "synthetic", [ExtractedPage(2, lines=[
        ExtractedLine("header", 2, 10, "HEADER"),
        ExtractedLine("transaction", 2, 15, "01/04/2026 SAME 10,00", bbox=box),
    ])], {})
    result = parsed(["", "HEADER\n01/04/2026 SAME 10,00"], [(2, 2)], extraction=extraction)
    region = bind_transaction_source_regions(result, b"scanned-pdf").canonical_transactions[0].source_regions[0]
    assert asdict(region) == {"page": 2, **box}


def test_explicit_multpage_references_highlight_only_original_evidence():
    from dataclasses import replace

    pages = (("HEADER", "ANCHOR"), ("CONTINUATION", "UNRELATED FOOTER"))
    result = parsed(["\n".join(page) for page in pages], [(1, 2)])
    result.canonical_transactions[0] = replace(result.canonical_transactions[0], source_line_refs=((1, 2), (2, 1)))
    regions = bind_transaction_source_regions(result, generate_native_text_pdf(pages)).canonical_transactions[0].source_regions
    assert [region.page for region in regions] == [1, 2]


def test_ocr_table_row_index_is_not_mistaken_for_a_line_index():
    result = parsed(["HEADER\nUNRELATED"], [(1, 2)], extraction=RawDocumentExtraction("aws_textract", "test", [
        ExtractedPage(1, lines=[ExtractedLine("head", 1, 1, "HEADER"), ExtractedLine("other", 1, 2, "UNRELATED",
            bbox={"left": 0.1, "top": 0.2, "width": 0.4, "height": 0.03})]),
    ], {}))
    result.canonical_transactions[0].source_parser = "textract_table"
    assert bind_transaction_source_regions(result, b"scan").canonical_transactions[0].source_regions == []


@pytest.mark.parametrize("bad", [None, {}, {"page": 0}, {"page": 1, "left": -0.1},
    {"page": 1, "top": float("nan")}, {"page": True}, {"page": 1, "width": 1.2}])
def test_invalid_regions_are_ignored_in_old_or_partial_payloads(bad):
    valid = {"page": 1, "left": 0.1, "top": 0.2, "width": 0.3, "height": 0.04}
    candidate = {**valid, **bad} if isinstance(bad, dict) else bad
    if bad == {}:
        candidate = {}
    assert parse_source_regions([candidate, valid]) == [SourceRegion(**valid)]
