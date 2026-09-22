from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.conversion.canonical_layout_capture import CanonicalLayoutGenerator
from app.application.conversion.canonical_layout_v2 import build_safe_layout_pages, match_layout_candidates
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.document_extraction_models import ExtractedLine


def _text_pdf(*lines: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    commands = ["BT", "/F1 10 Tf", "40 800 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -16 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_v2_keeps_public_phrases_and_replaces_customer_values_without_digits() -> None:
    pages, signals = build_safe_layout_pages(
        [{
            "width": 595.0,
            "height": 842.0,
            "source_text": (
                "BANCO PAN\nPERIODO DO EXTRATO\nCLIENTE CAIXA MARIA CPF 123.456.789-09\n"
                "SUAS MOVIMENTACOES\n03/01/2026 PIX RECEBIDO JOAO 1.234,56 C"
            ),
        }]
    )

    visible = "\n".join(element["text"] for element in pages[0]["elements"])
    assert "PERIODO DO EXTRATO" in visible
    assert "SUAS MOVIMENTACOES" in visible
    assert "PIX RECEBIDO" in visible
    assert "[DATA]" in visible
    assert "[VALOR_C]" in visible
    assert "MARIA" not in visible
    assert "JOAO" not in visible
    assert "123" not in visible
    assert "CAIXA" not in visible.split("CLIENTE", 1)[1]
    assert not any(character.isdigit() for character in visible)
    assert "SUAS MOVIMENTACOES" in signals["labels"]


def test_v2_uses_textract_line_boxes_instead_of_text_line_positions() -> None:
    pages, signals = build_safe_layout_pages(
        [{"width": 595.0, "height": 842.0, "source_text": "CAIXA\nVALOR SALDO"}],
        source_layout_lines=((
            ExtractedLine(
                id="a", page_number=1, line_index=1, text="CAIXA",
                bbox={"left": 0.14, "top": 0.09, "width": 0.2, "height": 0.03},
            ),
            ExtractedLine(
                id="b", page_number=1, line_index=2, text="VALOR SALDO",
                bbox={"left": 0.61, "top": 0.44, "width": 0.3, "height": 0.03},
            ),
        ),),
    )

    assert pages[0]["position_source"] == "textract"
    assert [(item["x"], item["y"]) for item in pages[0]["elements"]] == [(0.14, 0.09), (0.61, 0.44)]
    assert signals["geometry_source"] == "textract"


def test_v2_ranks_pan_profile_without_changing_conversion_label() -> None:
    source = "\n".join((
        "BANCO PAN", "PERIODO DO EXTRATO", "SALDO NESSE PERIODO", "ENTROU NA SUA CONTA",
        "SAIU DA SUA CONTA", "EXTRATO DE", "SUAS MOVIMENTACOES", "CREDITO PIX",
        "DEBITO PIX", "PIX RECEBIDO", "PIX ENVIADO", "AGENCIA", "CONTA",
    ))
    pages, signals = build_safe_layout_pages([{"width": 595.0, "height": 842.0, "source_text": source}])
    result = match_layout_candidates(signals=signals, bank_code="623")

    assert pages[0]["elements"]
    assert result["status"] == "existing_candidate"
    assert result["candidates"][0]["layout_name"] == "banco_pan_extrato_conta_pix_saldo_v1"
    assert result["candidates"][0]["matched_labels"]


def test_v2_matches_caixa_period_statement_when_ocr_splits_table_headers() -> None:
    source = "\n".join((
        "CAIXA",
        "EXTRATO POR PERIODO",
        "EXTRATO",
        "DATA M OV. NR. HISTORICO VALOR",
        "DOC.",
        "04/05/2026 123456 DEB IOF 26,54 D",
        "SALDO 10.026,54 D",
        "15/05/2026 654321 CRED TED 3.000,00 C",
        "SALDO 7.026,54 D",
    ))
    _, signals = build_safe_layout_pages([{"width": 595.0, "height": 842.0, "source_text": source}])

    result = match_layout_candidates(signals=signals, bank_code="104")

    assert result["status"] == "existing_candidate"
    assert result["candidates"][0]["layout_name"] == "caixa_extrato_por_periodo_web_v1"
    assert result["candidates"][0]["score"] >= 0.85


def test_v2_marks_unmatched_public_structure_for_new_layout_review() -> None:
    _, signals = build_safe_layout_pages([{
        "width": 595.0, "height": 842.0,
        "source_text": "EXTRATO\nDATA HISTORICO VALOR SALDO\nSALDO ANTERIOR",
    }])

    result = match_layout_candidates(signals=signals, bank_code="999")

    assert result == {"status": "new_candidate", "candidates": []}


def test_v2_generator_stores_no_source_values_in_pdf_or_manifest() -> None:
    source = _text_pdf(
        "CAIXA EXTRATO POR PERIODO",
        "CLIENTE MARIA SILVA CPF 12345678909",
        "DATA MOV NR DOC HISTORICO VALOR SALDO",
        "10/09/2026 PIX RECEBIDO JOAO 7.654,32 C",
    )
    generator = CanonicalLayoutGenerator(
        schema_version="2",
        capture_id_provider=lambda: "cap_0123456789abcdef01234567",
    )

    artifact = generator.generate(
        document=ingest_uploaded_document("statement.pdf", source),
        status="Sucesso", conversion_type="pdf-ofx", transactions_count=1,
        layout_name="generic_statement_ptbr", layout_confidence=0.7,
        selected_parser="inline", warning_count=0, balance_failed=0,
        bank_name="Caixa Economica Federal", bank_code="104",
    )

    visible = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    serialized = str(artifact.manifest)
    assert artifact.manifest["schema_version"] == "2"
    assert "EXTRATO POR PERIODO" in visible
    assert "[DATA]" in visible
    for forbidden in ("MARIA", "SILVA", "JOAO", "12345678909", "7654", "10/09/2026"):
        assert forbidden not in visible
        assert forbidden not in serialized
    assert artifact.privacy_validated


def test_v2_generator_uses_reused_textract_geometry() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    generator = CanonicalLayoutGenerator(
        schema_version="2",
        capture_id_provider=lambda: "cap_0123456789abcdef01234567",
    )

    artifact = generator.generate(
        document=ingest_uploaded_document("scanned.pdf", output.getvalue()),
        status="Sucesso", conversion_type="pdf-ofx", transactions_count=1,
        layout_name="generic_statement_ptbr", layout_confidence=0.7,
        selected_parser="inline", warning_count=0, balance_failed=0,
        bank_name="Caixa Economica Federal", bank_code="104",
        page_texts=("EXTRATO POR PERIODO\nCLIENTE MARIA",),
        page_text_source="ocr",
        source_layout_lines=((
            ExtractedLine(
                id="header", page_number=1, line_index=1, text="EXTRATO POR PERIODO",
                bbox={"left": 0.12, "top": 0.08, "width": 0.4, "height": 0.03},
            ),
        ),),
    )

    assert artifact.manifest["pages"][0]["position_source"] == "textract"
    assert artifact.manifest["pages"][0]["elements"][0]["x"] == 0.12
    assert artifact.manifest["layout_signals"]["geometry_source"] == "textract"


def test_v2_prefers_supplied_textract_lines_when_pdf_has_native_text() -> None:
    source = _text_pdf("NATIVE HEADER THAT IS NOT THE OCR RESULT")
    generator = CanonicalLayoutGenerator(schema_version="2")

    artifact = generator.generate(
        document=ingest_uploaded_document("mixed.pdf", source),
        status="Falha", conversion_type="pdf-ofx", transactions_count=0,
        layout_name=None, layout_confidence=None, selected_parser="textract",
        warning_count=0, balance_failed=0,
        page_texts=("EXTRATO POR PERIODO",), page_text_source="ocr",
        source_layout_lines=((ExtractedLine(
            id="ocr", page_number=1, line_index=1, text="EXTRATO POR PERIODO",
            bbox={"left": 0.13, "top": 0.07, "width": 0.4, "height": 0.03},
        ),),),
    )

    assert artifact.manifest["text_source"] == "ocr"
    assert artifact.manifest["pages"][0]["elements"][0]["text"] == "EXTRATO POR PERIODO"
    assert artifact.manifest["pages"][0]["elements"][0]["x"] == 0.13


def test_v2_does_not_store_unverified_bank_name() -> None:
    source = _text_pdf("EXTRATO CLIENTE MARIA SILVA")
    generator = CanonicalLayoutGenerator(schema_version="2")

    artifact = generator.generate(
        document=ingest_uploaded_document("bank.pdf", source),
        status="Falha", conversion_type="pdf-ofx", transactions_count=0,
        layout_name=None, layout_confidence=None, selected_parser="native",
        warning_count=0, balance_failed=0,
        bank_name="MARIA SILVA", bank_code=None,
    )

    assert artifact.manifest["bank"]["name"] == "unknown"
    assert "MARIA" not in str(artifact.manifest)


def test_v2_samples_first_five_pages_and_uses_bounded_textract_fallback() -> None:
    writer = PdfWriter()
    for _ in range(24):
        writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    calls = []

    def preview_extractor(raw_bytes):
        calls.append(raw_bytes)
        return (
            ("BANCO PAN\nPERIODO DO EXTRATO",) * 5,
            tuple((ExtractedLine(
                id=f"header-{index}", page_number=index + 1, line_index=1,
                text="PERIODO DO EXTRATO",
                bbox={"left": 0.15, "top": 0.08, "width": 0.5, "height": 0.03},
            ),) for index in range(5)),
        )

    generator = CanonicalLayoutGenerator(
        schema_version="2", max_pages=20, layout_preview_extractor=preview_extractor,
    )
    artifact = generator.generate(
        document=ingest_uploaded_document("long.pdf", output.getvalue()),
        status="Falha", conversion_type="pdf-ofx", transactions_count=0,
        layout_name=None, layout_confidence=None, selected_parser="textract",
        warning_count=0, balance_failed=0,
    )

    assert len(calls) == 1
    assert len(PdfReader(BytesIO(calls[0])).pages) == 5
    assert artifact.manifest["source_page_count"] == 24
    assert artifact.manifest["sampled_page_count"] == 5
    assert len(artifact.manifest["pages"]) == 5
    assert artifact.manifest["pages"][0]["position_source"] == "textract"
