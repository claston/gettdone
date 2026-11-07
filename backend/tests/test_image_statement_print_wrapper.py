"""Printed screenshots must reach OCR even when browser furniture is selectable."""

import hashlib
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.application import pdf_parser
from app.application.errors import MaxPagesPerFileExceededError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.native_text_quality import assess_native_text_quality
from app.application.pdf_representation_classifier import classify_pdf_representation
from tests.fixtures.structured_statements import bradesco_monthly_extraction


def printed_image_pdf(*, body_text=False, image_size=(280, 500), nested=False, image_painted=True, pages=1,
                      form_matrix="1 0 0 1 0 0", rotation=0):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        if rotation:
            page.rotate(rotation)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        image = DecodedStreamObject()
        image.set_data(b"\xff" * (900 * 1600))
        image.update({
            NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(900), NameObject("/Height"): NumberObject(1600),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
        })
        image_ref = writer._add_object(image.flate_encode())
        resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            NameObject("/XObject"): DictionaryObject({NameObject("/Image"): image_ref}),
        })
        image_commands = f"q {image_size[0]} 0 0 {image_size[1]} 155 160 cm /Image Do Q".encode()
        if nested:
            form = DecodedStreamObject()
            form.set_data(image_commands)
            form.update({
                NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, 0, 595, 842)]),
                NameObject("/Resources"): resources,
            })
            resources = DictionaryObject({
                NameObject("/Font"): resources["/Font"],
                NameObject("/XObject"): DictionaryObject({NameObject("/Form"): writer._add_object(form)}),
            })
            image_commands = f"q {form_matrix} cm /Form Do Q".encode()
        page[NameObject("/Resources")] = resources
        commands = [image_commands] if image_painted else []
        commands.extend([
            b"BT /F1 8 Tf 20 820 Td (06/11/2026, 09:00 Screenshot document reference 0123456789abcdef) Tj ET",
            b"BT /F1 8 Tf 20 20 Td (blob:https://example.invalid/01234567-89ab-cdef-0123-456789abcdef 1/1) Tj ET",
        ])
        if body_text:
            commands.append(b"BT /F1 10 Tf 170 600 Td (05/10/2026 PIX RECEBIDO 50,00) Tj ET")
        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(commands))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("options", [{}, {"nested": True}, {"rotation": 90}])
def test_print_wrapper_does_not_hide_a_document_image(options):
    raw = printed_image_pdf(**options)
    text = PdfReader(BytesIO(raw)).pages[0].extract_text()
    assert len(text.strip()) > 80
    quality = assess_native_text_quality(raw, [text])
    result = classify_pdf_representation(raw)
    assert quality.requires_visual_extraction
    assert quality.metrics()["native_text_quality_reason"] == "image_pages_without_usable_text"
    assert result.representation == "mixed"
    assert result.requires_visual_extraction
    assert "document_raster_present" in result.evidence


@pytest.mark.parametrize("options", [
    {"body_text": True}, {"image_size": (40, 60)}, {"image_painted": False},
    {"nested": True, "form_matrix": "0.1 0 0 0.1 0 0"},
    {"nested": True, "form_matrix": "1 0 0 1 700 0"},
    {"body_text": True, "rotation": 90},
])
def test_native_body_text_logos_and_unpainted_image_resources_do_not_force_ocr(options):
    assert not classify_pdf_representation(printed_image_pdf(**options)).requires_visual_extraction


def configure_gateway(monkeypatch, extraction=None):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    extraction = extraction or bradesco_monthly_extraction()
    gateway = Mock(return_value={"metrics": {"textract_mode": "text"}})
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: SimpleNamespace(analyze_pdf=gateway))
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: extraction)
    return gateway


def test_printed_image_uses_existing_ocr_reader_and_preserves_financial_values_and_provenance(monkeypatch):
    gateway = configure_gateway(monkeypatch)
    raw = printed_image_pdf()
    result = pdf_parser.parse_pdf_transactions(raw)
    gateway.assert_called_once()
    assert result.layout.layout_name == "bradesco_extrato_mensal_dia_historico_valor_ocr_v1"
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-09-11", 50), ("2026-09-15", -20)]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["native_text_quality_requires_visual"] == 1
    assert result.source_extraction.document_hash == hashlib.sha256(raw).hexdigest()
    assert all(row.source_regions for row in result.canonical_transactions)


def test_readable_ocr_movements_with_inconsistent_printed_balances_remain_available_for_review(monkeypatch):
    extraction = bradesco_monthly_extraction()
    page = extraction.pages[0]
    extraction = replace(extraction, pages=[replace(page, lines=[
        replace(line, text="0,00") if line.text == "100,00" else line for line in page.lines
    ])])
    configure_gateway(monkeypatch, extraction)
    result = pdf_parser.parse_pdf_transactions(printed_image_pdf())
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-09-11", 50), ("2026-09-15", -20)]
    assert result.parse_metrics["verified_opening_balance"] == 0
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_validation_status"] == "review_required"
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


def test_later_printed_image_page_cannot_be_silently_skipped(monkeypatch):
    gateway = configure_gateway(monkeypatch)
    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(printed_image_pdf(body_text=True))).pages[0])
    writer.add_page(PdfReader(BytesIO(printed_image_pdf())).pages[0])
    output = BytesIO()
    writer.write(output)
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(output.getvalue(), max_ocr_pages=1)
    gateway.assert_not_called()


def test_printed_image_with_disabled_ocr_surfaces_extraction_ambiguity(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(OcrFinancialAmbiguityError):
        pdf_parser.parse_pdf_transactions(printed_image_pdf())


def test_printed_image_keeps_financial_validation_failure_instead_of_using_print_wrapper(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway",
                        Mock(side_effect=PdfFinancialValidationError("printed controls do not reconcile")))
    with pytest.raises(PdfFinancialValidationError, match="printed controls do not reconcile"):
        pdf_parser.parse_pdf_transactions(printed_image_pdf())


@pytest.mark.real_http
@pytest.mark.parametrize("mismatched_opening", [False, True])
def test_printed_image_upload_and_reports_over_real_http(tmp_path, monkeypatch, mismatched_opening):
    from tests.ocr_conversion_http_helpers import public_client

    extraction = bradesco_monthly_extraction()
    if mismatched_opening:
        page = extraction.pages[0]
        extraction = replace(extraction, pages=[replace(page, lines=[
            replace(line, text="0,00") if line.text == "100,00" else line for line in page.lines
        ])])
    configure_gateway(monkeypatch, extraction)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-printed-image-ocr"}
        response = client.post("/convert", data=owner, files={
            "file": ("printed-image.pdf", printed_image_pdf(), "application/pdf"),
        })
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == 2
        assert analysis["opening_balance"] == (0 if mismatched_opening else 100)
        assert analysis["closing_balance"] == 130
        assert analysis["pdf_processing_metrics"]["textract_attempted"] == 1
        if mismatched_opening:
            assert analysis["pdf_processing_metrics"]["balance_validation_status"] == "review_required"
            assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
        aid = analysis["analysis_id"]
        assert client.get(f"/report/{aid}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{aid}", params={**owner, "format": fmt,
                "bank_code": "237", "bank_branch": "1234", "account_number": "123456-7"})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == 2
                if mismatched_opening:
                    assert "<LEDGERBAL>" not in report.text
                else:
                    assert "<BALAMT>130.00" in report.text
        supported = client.post("/convert", data=owner, files={
            "file": ("extrato.csv", b"date,description,amount\n2026-09-11,CREDITO,50\n", "text/csv"),
        })
        assert supported.status_code == 200, supported.text
        assert client.get("/report/an_printed_image_missing", params=owner).status_code == 404
        # The legacy upload endpoint stays disabled in production.
        assert client.post("/analyze").status_code == 405
