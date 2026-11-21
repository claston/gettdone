"""Fictional six-page scans with Textract joining history and document cells."""
from dataclasses import replace

import pytest

from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages
from app.application.pdf_review_guidance import build_pdf_review_guidance
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_oct20_followup_ocr import ocr

pytestmark = pytest.mark.pdf_acceptance


def bradesco_six_pages(*, gap=False, merged=True):
    pages = []
    balance = -100
    for page in range(1, 7):
        header = [
            ("Extrato Mensal / Por Período bradesco net empresa", .1, .03),
            (f"Folha {page}/6", .84, .06),
            ("Data", .03, .20), ("Lançamento", .14, .20), ("Dcto.", .45, .20),
            ("Crédito (R$)", .59, .20), ("Débito (R$)", .76, .20), ("Saldo (R$)", .92, .20),
        ]
        if page == 1:
            header += [("31/08/2026", .03, .25), ("SALDO ANTERIOR", .14, .25), ("-100,00", .93, .25)]
        if gap and page == 2:
            balance += 7
        for index, amount in enumerate((50, -20)):
            y = .35 + index * .05
            balance += amount
            header += [
                (f"{page:02}/09/2026", .03, y), ("TRANSFERENCIA CLIENTE 123", .14, y),
                (f"{page:02}{index:07}", .45, y),
                (f"{amount},00", .63 if amount > 0 else .79, y), (f"{balance},00", .93, y),
            ]
        pages.append(header)
    source = ocr(pages)
    for page in source.pages:
        # A repeated summary label crosses the document-column boundary above
        # the transaction header. It must not become a standalone TOTAL row.
        template = page.lines[0]
        words = [ExtractedWord(f"summary-{page.page_number}-{i}", page.page_number, i, text,
                 bbox={"left": x, "top": .12, "width": .04, "height": .006})
                 for i, (text, x) in enumerate([("Total", .41), ("Disponível", .46), ("(R$)", .54)], 1)]
        page.words.extend(words)
        page.lines.append(replace(template, id=f"summary-{page.page_number}", line_index=100,
            text="Total Disponível (R$)", bbox={"left": .41, "top": .12, "width": .18, "height": .006},
            word_ids=tuple(word.id for word in words)))
    if merged:
        for page in source.pages[1::2]:
            history = next(line for line in page.lines if line.text == "TRANSFERENCIA CLIENTE 123")
            document = next(line for line in page.lines if line.text == f"{page.page_number:02}0000000")
            words = [ExtractedWord(f"{history.id}-w{i}", page.page_number, i, text,
                     bbox={"left": x, "top": history.bbox["top"], "width": .05, "height": .006})
                     for i, (text, x) in enumerate([
                         ("TRANSFERENCIA", .14), ("CLIENTE", .26), ("123", .36), (document.text, .45)], 1)]
            page.words.extend(words)
            page.lines[page.lines.index(history)] = replace(history,
                text=f"{history.text} {document.text}", bbox={**history.bbox, "width": .36},
                word_ids=tuple(word.id for word in words))
            page.lines.remove(document)
    return source


def test_six_page_ocr_splits_joined_document_using_word_positions_and_keeps_provenance():
    source = bradesco_six_pages()
    result = _parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [50, -20] * 6
    assert [row.source_page for row in result.canonical_transactions] == [p for p in range(1, 7) for _ in range(2)]
    assert [row.date for row in result.transactions] == [f"2026-09-{p:02}" for p in range(1, 7) for _ in range(2)]
    assert all(row.description == "TRANSFERENCIA CLIENTE 123" for row in result.transactions)
    assert [row.external_reference_id for row in result.canonical_transactions] == [
        f"{p:02}{i:07}" for p in range(1, 7) for i in range(2)]
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == 80
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["printed_balance_checks"] == 12


@pytest.mark.parametrize("merged", [False, True])
def test_page_balance_gap_preserves_printed_balances_and_requires_review(merged):
    result = _parse_scanned_pdf_with_textract_text_pages(bradesco_six_pages(gap=True, merged=merged))
    assert [row.amount for row in result.transactions] == [50, -20] * 6
    assert [row.running_balance for row in result.canonical_transactions][:4] == [-50, -70, -13, -33]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == 87
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert any("financial_validation_failed" in row.warnings for row in result.canonical_transactions)


@pytest.mark.parametrize("damage", ["missing_words", "wrong_column", "unreadable_document", "missing_word"])
def test_joined_document_without_complete_positional_evidence_still_rejects(damage):
    source = bradesco_six_pages()
    page = source.pages[1]
    if damage == "missing_words":
        page.words.clear()
    elif damage == "missing_word":
        page.words.pop(-2)
    else:
        word = page.words[-1]
        page.words[-1] = replace(word, **(
            {"bbox": {**word.bbox, "left": .37}} if damage == "wrong_column" else {"text": "??"}))
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.parametrize("pro", [False, True], ids=["free", "pro"])
def test_six_page_review_survives_real_http_conversion_and_exports(monkeypatch, tmp_path, pro):
    from app.application import pdf_parser

    source = bradesco_six_pages(gap=True)
    blocks = []
    for page in source.pages:
        for kind, items in (("LINE", page.lines), ("WORD", page.words)):
            for item in items:
                block = {"Id": item.id, "BlockType": kind, "Page": page.page_number, "Text": item.text,
                         "Confidence": 99, "Geometry": {"BoundingBox": {k.title(): v for k, v in item.bbox.items()}}}
                if kind == "LINE" and item.word_ids:
                    block["Relationships"] = [{"Type": "CHILD", "Ids": list(item.word_ids)}]
                blocks.append(block)
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "true")
    monkeypatch.setenv("TEXTRACT_TEMP_BUCKET", "synthetic-unused-bucket")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", lambda *a, **kw: {
        "document_hash": "fictional-bradesco-six", "page_count": 6,
        "blocks": blocks, "metrics": {"textract_mode": "text"}})
    raw = generate_native_text_pdf((("Synthetic OCR source",),) * 6)

    def verify(client, analysis, owner):
        assert analysis["transactions_total"] == 12
        assert analysis["opening_balance"] == -100 and analysis["closing_balance"] == 87
        assert analysis["pdf_processing_metrics"]["balance_consistency_failed"] == 1
        assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
        assert build_pdf_review_guidance(analysis)["category"] == "financial_discrepancy"
        for fmt in ("csv", "xlsx", "ofx"):
            response = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": fmt})
            assert response.status_code == 200, response.text
            if fmt == "ofx":
                assert response.text.count("<STMTTRN>") == 12
                assert "<BALAMT>87.00" in response.text

    if pro:
        with worker_case(tmp_path, raw) as (client, job):
            verify(client, job["result"]["analysis"], {})
    else:
        with public_client(tmp_path) as client:
            owner = {"anonymous_fingerprint": "fictional-bradesco-six"}
            response = client.post("/convert", data=owner, files={"file": ("synthetic.pdf", raw, "application/pdf")})
            assert response.status_code == 200, response.text
            analysis = response.json()["analysis"]
            verify(client, analysis, owner)
            assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
            assert client.get("/report/an_missing_bradesco", params=owner).status_code == 404
