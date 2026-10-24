"""Acceptance travels through real upload/report/download and worker boundaries."""
from io import BytesIO

import pytest
from openpyxl import load_workbook

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import bb_outside_header, bradesco_monthly_native, sicredi_combined, xp_investment_pages
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case

pytestmark = [pytest.mark.real_http, pytest.mark.pdf_acceptance]


def document(kind, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind.startswith("sicredi"):
        return configure_ocr(monkeypatch, sicredi_combined(), "tables" if kind.endswith("tables") else "text"), 3
    if kind == "xp":
        return generate_native_text_pdf(tuple(p.splitlines() for p in xp_investment_pages())), 2
    return (bradesco_monthly_native(), 3) if kind == "bradesco" else (bb_outside_header(), 2)


@pytest.mark.parametrize("kind", ["bradesco", "bb", "sicredi_text", "sicredi_tables", "xp"])
def test_public_recovery_cases_preserve_rows_and_export_semantics(kind, tmp_path, monkeypatch):
    raw, count = document(kind, monkeypatch)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-reader-acceptance"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        aid = analysis["analysis_id"]
        assert client.get(f"/report/{aid}", params=owner).status_code == 200
        csv = client.get(f"/convert-report/{aid}", params={**owner, "format": "csv"})
        assert csv.status_code == 200
        xlsx = client.get(f"/convert-report/{aid}", params={**owner, "format": "xlsx"})
        assert xlsx.status_code == 200
        ofx = client.get(f"/convert-report/{aid}", params={**owner, "format": "ofx", "bank_code": "237",
            "bank_branch": "1234", "account_number": "12345-6"})
        if kind == "xp":
            assert ofx.status_code == 400 and "OFX" in ofx.text
            assert "dates.settlement" in csv.text
            assert analysis["financial_document"]["origin"] == "XP Investimentos"
            workbook = load_workbook(BytesIO(xlsx.content))
            assert "Data de liquidação" in [cell.value for cell in workbook["Eventos"][1]]
        else:
            assert ofx.status_code == 200 and ofx.text.count("<STMTTRN>") == count
        assert client.get("/report/an_nonexistent", params=owner).status_code == 404


@pytest.mark.parametrize("kind", ["bradesco", "bb", "sicredi_text", "xp"])
def test_worker_completes_same_documents_and_preserves_download_policy(kind, tmp_path, monkeypatch):
    raw, count = document(kind, monkeypatch)
    with worker_case(tmp_path, raw) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == count
        if kind == "bb":
            assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        if kind == "xp":
            assert analysis["document_type"] == "investment_statement"
            assert analysis["financial_document"]["origin"] == "XP Investimentos"
        for fmt in ["csv", "xlsx"]:
            assert client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": fmt}).status_code == 200
