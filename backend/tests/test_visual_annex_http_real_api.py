"""Synthetic image PDF through the PRO upload, worker and report boundary."""
from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.application.document_extraction_models import RawDocumentExtraction
from tests.ocr_conversion_http_helpers import configure_ocr, worker_case
from tests.test_compound_pdf import _line, _monthly_page
from tests.test_pdf_representation_classifier import _full_page_raster_pdf
from tests.test_visual_annex_coverage import automatic_details, fiscal_page

pytestmark = [pytest.mark.real_http, pytest.mark.pdf_acceptance]


def test_pro_worker_converts_image_statement_with_dated_annexes(tmp_path, monkeypatch):
    monthly = _monthly_page()
    monthly = replace(monthly, lines=[
        replace(line, text="350,00") if line.id == "credit-merged" else line for line in monthly.lines
    ] + [
        _line("debit", "20,00-", .71, .215),
        _line("identity", "ItaúEmpresas extrato mensal Conta Corrente Movimentação créditos débitos", .02, .01),
    ])
    fiscal = fiscal_page()
    fiscal = replace(fiscal, page_number=3, lines=[replace(line, page_number=3) for line in fiscal.lines])
    source = RawDocumentExtraction("aws_textract", "synthetic", [monthly, automatic_details(), fiscal], {})
    source = replace(source, pages=[replace(page, lines=[
        replace(line, id=f"page-{page.page_number}-{line.id}") for line in page.lines
    ]) for page in source.pages])
    configure_ocr(monkeypatch, source)
    writer = PdfWriter()
    image_page = PdfReader(BytesIO(_full_page_raster_pdf())).pages[0]
    for _ in source.pages:
        writer.add_page(image_page)
    output = BytesIO()
    writer.write(output)
    with worker_case(tmp_path, output.getvalue()) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == 2
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        aid = analysis["analysis_id"]
        assert client.get(f"/convert-report/{aid}", params={"format": "xlsx"}).status_code == 200
        csv = client.get(f"/convert-report/{aid}", params={"format": "csv"})
        assert csv.status_code == 200
        assert "350" in csv.text and "-20" in csv.text
        assert client.get("/report/an_nonexistent").status_code == 404
