import pytest

from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_xp_empresas_statement import AMOUNTS, LAYOUT, xp_pages, xp_pdf

pytestmark = pytest.mark.real_http


def assert_xp_analysis(analysis):
    assert analysis["transactions_total"] == 7
    assert [row["amount"] for row in analysis["preview_transactions"]] == AMOUNTS
    assert analysis["opening_balance"] == 1000
    assert analysis["closing_balance"] == 950
    assert analysis["total_inflows"] == 800
    assert analysis["total_outflows"] == -850
    assert analysis["bank_code"] == "348"
    assert analysis["bank_branch"] == "0001"
    assert analysis["account_number"] == "12345678"
    assert analysis["layout_inference_name"] == LAYOUT
    assert analysis["pdf_processing_metrics"]["canonical_warning_count"] == 0


def test_public_xp_conversion_reports_and_csv_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-xp-native"}
        response = client.post("/convert", data=owner, files={"file": ("xp.pdf", xp_pdf(), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert_xp_analysis(analysis)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            params = {**owner, "format": fmt}
            if fmt == "ofx":
                # Match the preview's existing bank/account confirmation fields.
                params.update(bank_branch=analysis["bank_branch"], account_number=analysis["account_number"],
                              bank_code=analysis["bank_code"], closing_balance=analysis["closing_balance"])
            report = client.get(f"/convert-report/{analysis_id}", params=params)
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == 7
                assert "<BANKID>348" in report.text
                assert "<ACCTID>12345678" in report.text
                assert "<BALAMT>950.00" in report.text
        assert client.get("/report/an_xp_missing", params=owner).status_code == 404
        # /analyze is intentionally disabled in the current API; /convert is the active upload endpoint.
        assert client.post("/analyze").status_code == 405
        csv = client.post("/convert", data=owner, files={
            "file": ("statement.csv", b"date,description,amount\n2026-07-30,PIX,100\n", "text/csv"),
        })
        assert csv.status_code == 200, csv.text
        assert csv.json()["analysis"]["transactions_total"] == 1


def test_http_xp_refuses_inconsistent_daily_balance(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = [page.replace("Saldo do dia R$ 1.150,00", "Saldo do dia R$ 1.151,00") for page in xp_pages()]
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-xp-invalid"}, files={
            "file": ("xp.pdf", xp_pdf(pages), "application/pdf"),
        })
        assert response.status_code == 400, response.text
        assert "XP Empresas" in response.text


def test_xp_worker_preserves_financials_and_ofx(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, xp_pdf(), filename="xp.pdf") as (client, job):
        assert_xp_analysis(job["result"]["analysis"])
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200, report.text
        assert report.text.count("<STMTTRN>") == 7
        assert "<BALAMT>950.00" in report.text
