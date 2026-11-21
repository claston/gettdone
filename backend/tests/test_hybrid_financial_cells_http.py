"""Hybrid tables must recover all rows or stop before returning partial exports."""
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.application.pdf_review_guidance import build_pdf_review_guidance
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_hybrid_financial_cells import hybrid_cells_pdf
from tests.test_image_statement_print_wrapper import configure_gateway
from tests.test_oct20_followup_ocr import ocr
from tests.test_protocol_daily_cash import protocol_pages

pytestmark = pytest.mark.real_http


def _hybrid_protocol_pdf():
    pages = protocol_pages()
    pages[-1] = [cell for cell in pages[-1] if cell[0] != "+10,00"]
    return hybrid_cells_pdf(table_pages=pages, row_y=.28)


def _verify(analysis):
    assert analysis["transactions_total"] == 5
    assert analysis["opening_balance"] == 100 and analysis["closing_balance"] == 145
    metrics = analysis["pdf_processing_metrics"]
    assert metrics["textract_attempted"] == 1
    assert metrics["financial_controls_validated"] == 0
    assert "inferido" in build_pdf_review_guidance(analysis)["message"]


def test_hybrid_free_upload_recovers_raster_row_and_exports_with_inferred_opening_warning(tmp_path, monkeypatch):
    gateway = configure_gateway(monkeypatch, ocr(protocol_pages()))
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "hybrid-free"}
        response = client.post("/convert", data=owner, files={"file": ("hybrid.pdf", _hybrid_protocol_pdf(), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        _verify(analysis)
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == 5
        assert client.get("/report/an_hybrid_missing", params=owner).status_code == 404
    gateway.assert_called_once()


def test_hybrid_pro_worker_recovers_raster_row_and_preserves_export(tmp_path, monkeypatch):
    gateway = configure_gateway(monkeypatch, ocr(protocol_pages()))
    with worker_case(tmp_path, _hybrid_protocol_pdf()) as (client, job):
        _verify(job["result"]["analysis"])
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200 and report.text.count("<STMTTRN>") == 5
    gateway.assert_called_once()


def test_ocr_limit_stops_hybrid_upload_without_partial_conversion_or_ocr_charge(tmp_path, monkeypatch):
    gateway = configure_gateway(monkeypatch, ocr(protocol_pages()))
    writer = PdfWriter()
    page = PdfReader(BytesIO(hybrid_cells_pdf())).pages[0]
    for _ in range(11):
        writer.add_page(page)
    stream = BytesIO()
    writer.write(stream)
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "hybrid-limit"},
            files={"file": ("hybrid.pdf", stream.getvalue(), "application/pdf")})
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "pages_limit_exceeded"
        assert detail["pages_count"] == 11 and detail["max_pages_per_file"] == 10
        assert detail["ocr_context"] == "scanned_pdf"
        assert "escaneado" in detail["message"]
    gateway.assert_not_called()
