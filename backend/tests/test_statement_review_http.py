"""The review policy applies to uploads, workers and report downloads."""

from dataclasses import replace

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.structured_statements import bradesco_monthly_extraction
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_recovery_case_fixes import mercado_pago_page
from tests.test_statement_review_policy import SANTANDER

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["santander", "bradesco_text", "bradesco_tables", "mercado_pago_mismatch"])
def test_worker_completes_readable_statements_with_review_warnings_and_downloads(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    warning = "partial_statement"
    if kind.startswith("bradesco"):
        source = bradesco_monthly_extraction()
        page = source.pages[0]
        lines = [replace(line, text="Página: 3 de 8") if line.text == "Página: 1 de 1" else line for line in page.lines]
        source = replace(source, pages=[replace(page, lines=lines)])
        raw = configure_ocr(monkeypatch, source, "tables" if kind.endswith("tables") else "text")
    else:
        text = SANTANDER
        if kind == "mercado_pago_mismatch":
            warning = "financial_validation_failed"
            text = mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 71,00")
        raw = generate_native_text_pdf((text.splitlines(),))
    with worker_case(tmp_path, raw) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == (3 if kind == "mercado_pago_mismatch" else 2)
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        assert warning in analysis["preview_transactions"][0]["warning_types"]
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": fmt})
            assert report.status_code == 200
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == analysis["transactions_total"]


def test_public_summary_mismatch_keeps_values_and_reports_review_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(((mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 71,00")).splitlines(),))
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-review-policy"},
                               files={"file": ("statement.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == [50, -20, 20]
        assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
        assert "financial_validation_failed" in analysis["preview_transactions"][0]["warning_types"]
