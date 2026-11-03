"""Issuer identity reaches public and worker OFX exports without changing the grid."""
import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_santander_ocr_controls import santander_extraction
from tests.test_statement_issuer_evidence import issuer_pdf

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("footer", [False, True])
def test_public_conversion_exports_the_explicit_issuer(tmp_path, monkeypatch, footer):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-issuer"}
        response = client.post("/convert", data=owner,
            files={"file": ("fictional.pdf", issuer_pdf(footer=footer), "application/pdf")})
        assert response.status_code == 200, response.text
        payload = response.json()
        analysis = payload["analysis"]
        assert analysis["bank_name"] == "Santander"
        assert analysis["transactions_total"] == 3
        assert analysis["layout_inference_name"] == "account_detail_descending_cash_v1"
        assert (analysis["opening_balance"], analysis["closing_balance"]) == (100, 120)
        ofx = client.get(f"/convert-report/{payload['processing_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 3
        for amount in (-10, 50, -20):
            assert f"<TRNAMT>{amount:.2f}" in ofx.text
        assert client.get(f"/report/{payload['processing_id']}", params=owner).status_code == 200
        assert client.get("/report/an_issuer_nonexistent", params=owner).status_code == 404


def test_worker_preserves_footer_identity_in_stored_report(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with worker_case(tmp_path, issuer_pdf(footer=True)) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["bank_name"] == "Santander"
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 3


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_ocr_header_identity_reaches_the_export(tmp_path, monkeypatch, mode):
    raw = configure_ocr(monkeypatch, santander_extraction(), mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-ocr-issuer"}
        response = client.post("/convert", data=owner,
            files={"file": ("fictional.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["bank_name"] == "Santander"
        assert analysis["transactions_total"] == 7
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 7
