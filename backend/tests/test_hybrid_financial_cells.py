"""Fictional PDFs with small financial raster cells amongst readable text."""
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.application.conversion.document_preflight_service import DocumentPreflightService
from app.application.errors import OcrFinancialAmbiguityError
from app.application.native_text_quality import assess_native_text_quality
from app.application.pdf_parser import parse_pdf_transactions
from app.application.pdf_representation_classifier import classify_pdf_representation
from tests.fixtures.recovery_acceptance import grid_pdf


def hybrid_cells_pdf(*, native_amount=False, logo=False, painted=True, later_page=False, table_pages=None, row_y=.25):
    header = [("Extrato financeiro Banco Teste", .06, .03), ("Data", .06, .12),
              ("Descrição", .20, .12), ("Protocolo", .66, .12), ("Valor (R$)", .86, .12)]
    row = [("01/04/2026", .06, .25), ("PIX RECEBIDO EMPRESA FICTÍCIA", .20, .25), ("1000000001", .66, .25)]
    if native_amount or logo:
        row.append(("+50,00", .86, .25))
    pages = [header + row] if not later_page else [header + [("01/04/2026 PIX TESTE +10,00", .06, .20)], row]
    pages = table_pages or pages
    writer = PdfWriter(clone_from=BytesIO(grid_pdf(pages)))
    page = writer.pages[-1]
    image = DecodedStreamObject()
    image.set_data(bytes([0, 255] * 400))
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(80), NameObject("/Height"): NumberObject(10),
                  NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8)})
    page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Cell"): writer._add_object(image)})
    stream = DecodedStreamObject()
    x, y = (860, 750) if logo else (860, 800 * (1 - row_y) - 2)
    stream.set_data(page.get_contents().get_data() + (f"\nq 80 0 0 10 {x} {y} cm /Cell Do Q".encode() if painted else b""))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("later_page", [False, True])
def test_small_missing_amount_image_requires_visual_reading_even_with_dense_native_text(later_page):
    raw = hybrid_cells_pdf(later_page=later_page)
    pages = [page.extract_text() for page in PdfReader(BytesIO(raw)).pages]
    quality = assess_native_text_quality(raw, pages)
    assert quality.requires_visual_extraction
    assert "rasterized_financial_cells" in quality.evidence
    classification = classify_pdf_representation(raw)
    assert classification.requires_visual_extraction
    assert classification.representation == "mixed"


@pytest.mark.parametrize("options", [{"logo": True}, {"painted": False}, {"native_amount": True}])
def test_logos_unused_resources_and_cells_with_native_values_do_not_force_ocr(options):
    assert not classify_pdf_representation(hybrid_cells_pdf(**options)).requires_visual_extraction


def test_unavailable_ocr_cannot_return_a_partial_native_conversion(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse_pdf_transactions(hybrid_cells_pdf(later_page=True))


@pytest.mark.parametrize("staged", [False, True])
def test_preflight_counts_mixed_pages_as_requiring_ocr(tmp_path, staged):
    raw = hybrid_cells_pdf(later_page=True)
    service = DocumentPreflightService()
    if staged:
        path = tmp_path / "hybrid.pdf"
        path.write_bytes(raw)
        result = service.inspect_staged_upload(filename=path.name, staged_path=path)
    else:
        result = service.inspect_raw_bytes(filename="hybrid.pdf", raw_bytes=raw)
    assert result.estimated_pages_count == 2
    assert result.scanned_likely is True
