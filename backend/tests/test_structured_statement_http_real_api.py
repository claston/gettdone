"""Synthetic bank/report layouts through real upload, download and worker paths."""

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.structured_statements import (
    bradesco_monthly_extraction,
    conta_azul_pages,
    cresol_inactive_pages,
    santander_pages,
    sumup_pages,
)
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("factory,layout,count", [
    (sumup_pages, "sumup_extrato_periodo_data_hora_v1", 4),
    (santander_pages, "santander_internet_banking_empresarial_periodo_agrupado_v1", 4),
    (cresol_inactive_pages, "cresol_posicao_consolidada_sem_movimento_v1", 0),
    (conta_azul_pages, "conta_azul_relatorio_extrato_valor_saldo_v1", 2),
])
def test_native_structures_convert_and_download_over_http(factory, layout, count, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in factory()))
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-structured-native"}
        response = client.post("/convert", data=owner, files={"file": ("extrato.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        assert analysis["layout_inference_name"] == layout
        assert analysis["pdf_processing_metrics"]["textract_attempted"] == 0
        identifier = analysis["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx"):
            report = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
        csv = client.post("/convert", data=owner, files={
            "file": ("extrato.csv", b"date,description,amount\n2026-09-01,CREDITO,100\n", "text/csv"),
        })
        assert csv.status_code == 200 and csv.json()["analysis"]["transactions_total"] == 1
        assert client.get("/report/an_structured_missing", params=owner).status_code == 404
        # /convert is the production upload route; /analyze remains disabled.
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_bradesco_ocr_exports_correct_dates_and_financials_over_http(tmp_path, monkeypatch, mode):
    raw = configure_ocr(monkeypatch, bradesco_monthly_extraction(), mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-bradesco-monthly-ocr"}
        response = client.post("/convert", data=owner, files={"file": ("bradesco.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == 2
        assert analysis["closing_balance"] == 130
        assert [row["date"] for row in analysis["preview_transactions"]] == ["2026-09-11", "2026-09-15"]
        report = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx",
                            "bank_code": "237", "bank_branch": "1234", "account_number": "123456-7",
                            "closing_balance": analysis["closing_balance"]})
        assert report.status_code == 200, report.text
        assert report.text.count("<STMTTRN>") == 2
        assert "<BALAMT>130.00" in report.text


def test_cresol_worker_keeps_inactive_balance_without_creating_transactions(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in cresol_inactive_pages()))
    with worker_case(tmp_path, raw, filename="cresol-sem-movimento.pdf") as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == 0
        assert analysis["opening_balance"] == analysis["closing_balance"] == 30.93
        assert analysis["pdf_processing_metrics"]["statement_no_activity"] == 1
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200, report.text
        assert "<STMTTRN>" not in report.text
        assert "<BALAMT>30.93" in report.text
