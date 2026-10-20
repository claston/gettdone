import hashlib
from dataclasses import replace
from io import BytesIO
from unittest.mock import Mock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser
from app.application.document_extraction_models import RawDocumentExtraction
from app.application.errors import MaxPagesPerFileExceededError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.models import NormalizedTransaction
from app.application.parsers.pdf.models import PdfParseResult
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.pdf_representation_classifier import classify_pdf_representation
from tests.test_pdf_representation_classifier import _full_page_raster_pdf

BAD_TEXT = "\n".join(
    [
        "Banco Exemplo Agencia 0001 Conta 12345",
        "DATA DESCRICAO CREDITO DEBITO SALDO",
        "SALDO ANTERIOR 1.000, 00",
        "03/08/2026 CREDITO PIX 100,00 1.100, 00",
        "©3/08/2026 DEBITO PIX -50,00 1.050, 00",
        "©4/08/2026 DEBITO PIX -10,00 1.040, 00",
    ]
)


def layered_pdf(text=BAD_TEXT, *, invisible=True, raster=True, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        if raster:
            source = PdfReader(BytesIO(_full_page_raster_pdf()))
            page = writer.add_page(source.pages[0])
            existing = page.get_contents().get_data()
        else:
            page = writer.add_blank_page(width=595, height=842)
            page[NameObject("/Resources")] = DictionaryObject()
            existing = b""
        font = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }
            )
        )
        page["/Resources"][NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font})
        commands = [f"BT /F1 8 Tf {3 if invisible else 0} Tr 40 790 Td"]
        for index, line in enumerate(text.splitlines()):
            if index:
                commands.append("0 -14 Td")
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data(existing + b"\n" + "\n".join(commands).encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def visual_result():
    return PdfParseResult(
        transactions=[NormalizedTransaction(date="2026-08-03", description="PIX CONFIRMADO", amount=100, type="inflow")],
        layout=PdfLayoutInference("generic_statement_ptbr", 0.8, True),
        extracted_text="03/08/2026 PIX CONFIRMADO 100,00",
        parse_metrics={"extraction_provider": "aws_textract", "textract_used": 1},
    )


def test_image_with_broken_invisible_text_requires_visual_extraction():
    result = classify_pdf_representation(layered_pdf())
    assert result.representation == "mixed"
    assert result.requires_visual_extraction
    assert "invisible_text_layer_present" in result.evidence
    assert "embedded_text_quality_low" in result.evidence


@pytest.mark.parametrize("invisible,raster", [(False, True), (True, False)])
def test_malformed_text_alone_does_not_claim_an_embedded_ocr_image(invisible, raster):
    result = classify_pdf_representation(layered_pdf(invisible=invisible, raster=raster))
    assert not result.requires_visual_extraction
    assert "embedded_text_quality_low" not in result.evidence


def test_healthy_invisible_text_can_be_read_without_a_new_ocr():
    text = BAD_TEXT.replace("©", "0").replace(", ", ",")
    result = classify_pdf_representation(layered_pdf(text))
    assert result.representation == "mixed"
    assert not result.requires_visual_extraction


def test_bad_embedded_text_uses_visual_gateway_even_when_native_text_exists(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    original = layered_pdf()
    result = pdf_parser.parse_pdf_transactions(original)
    gateway.assert_called_once()
    visual_input = gateway.call_args.args[0]
    assert visual_input != original
    assert not any(page.extract_text() for page in PdfReader(BytesIO(visual_input)).pages)
    assert "©3/08/2026" in PdfReader(BytesIO(original)).pages[0].extract_text()
    assert PdfReader(BytesIO(visual_input)).pages[0].images[0].data == PdfReader(BytesIO(original)).pages[0].images[0].data
    assert result.transactions[0].amount == 100
    assert result.parse_metrics["native_text_detected"] == 1
    assert result.parse_metrics["native_text_quality_requires_visual"] == 1


def test_visual_reading_without_independent_financial_controls_keeps_review_warning(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", Mock(return_value=visual_result()))
    result = pdf_parser.parse_pdf_transactions(layered_pdf())
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert all("manual_review_recommended" in row.warnings for row in result.canonical_transactions)


def test_disabled_visual_extraction_refuses_unreliable_native_conversion(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(OcrFinancialAmbiguityError):
        pdf_parser.parse_pdf_transactions(layered_pdf())


def test_visual_failure_does_not_return_to_the_broken_native_layer(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    monkeypatch.setattr(
        pdf_parser, "_parse_scanned_pdf_with_textract_gateway", Mock(side_effect=PdfFinancialValidationError("visual controls failed"))
    )
    with pytest.raises(PdfFinancialValidationError, match="visual controls failed"):
        pdf_parser.parse_pdf_transactions(layered_pdf())


def test_quality_gate_keeps_existing_ocr_page_limit(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(layered_pdf(pages=2), max_ocr_pages=1)
    gateway.assert_not_called()


def test_unreliable_layer_can_use_the_configured_local_visual_adapter(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "true")
    adapter = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_local_ocr_adapter", adapter)
    result = pdf_parser.parse_pdf_transactions(layered_pdf())
    adapter.assert_called_once()
    assert result.parse_metrics["native_text_quality_requires_visual"] == 1


def test_healthy_hidden_layer_does_not_force_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    raw = layered_pdf(BAD_TEXT.replace("©", "0").replace(", ", ","))
    assert pdf_parser.parse_pdf_transactions(raw).transactions
    gateway.assert_not_called()


def test_visual_source_preserves_original_document_hash_and_records_derivative_hash(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    result = replace(visual_result(), source_extraction=RawDocumentExtraction("aws_textract", "derived", [], {}))
    gateway = Mock(return_value=result)
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    original = layered_pdf()
    parsed = pdf_parser.parse_pdf_transactions(original)
    assert parsed.source_extraction.document_hash == hashlib.sha256(original).hexdigest()
    assert parsed.source_extraction.metrics["visual_input_sha256"] == hashlib.sha256(gateway.call_args.args[0]).hexdigest()
