"""Public conversion and PRO worker regressions using synthetic documents."""

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_oct24_review_fixes import invoice_pdf, santander_page

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["nubank", "santander"])
def test_pro_worker_preserves_invoice_signs_and_partial_warning(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = invoice_pdf() if kind == "nubank" else generate_native_text_pdf((santander_page().splitlines(),))
    with worker_case(tmp_path, raw) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == (5 if kind == "nubank" else 2)
        if kind == "nubank":
            assert analysis["layout_inference_name"] == "nubank_fatura_cartao_credito_v1"
            assert analysis["ofx_account_type"] == "credit_card"
            assert analysis["opening_balance"] == -150
            assert analysis["closing_balance"] == -200
        else:
            assert analysis["pdf_processing_metrics"]["partial_statement"] == 1
            assert "partial_statement" in analysis["preview_transactions"][0]["warning_types"]
            assert analysis["account_number"] == "123456789"
        downloaded = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "ofx"})
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.text.count("<STMTTRN>") == analysis["transactions_total"]
        if kind == "nubank":
            assert "<CREDITCARDMSGSRSV1>" in downloaded.text
            assert "<BALAMT>-200.00" in downloaded.text
        else:
            assert "<ACCTID>123456789" in downloaded.text


def test_public_export_preserves_twenty_digit_account_and_rejects_missing_report(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        fingerprint = "fp-account-length"
        response = client.post("/convert", data={"anonymous_fingerprint": fingerprint}, files={
            "file": ("synthetic.csv", b"date,description,amount\n2026-09-01,PIX,100\n", "text/csv"),
        })
        assert response.status_code == 200, response.text
        analysis_id = response.json()["analysis"]["analysis_id"]
        account = "00012345678901234567"
        params = {"anonymous_fingerprint": fingerprint}
        workbook = client.get(f"/report/{analysis_id}", params=params)
        assert workbook.status_code == 200, workbook.text
        edited = client.post(f"/convert-edits/{analysis_id}", params=params,
                             json={"edits": [], "bank_code": "033", "bank_branch": "1234", "account_number": account})
        assert edited.status_code == 200, edited.text
        downloaded = client.get(f"/convert-report/{analysis_id}", params={**params, "format": "ofx"})
        assert downloaded.status_code == 200, downloaded.text
        assert f"<ACCTID>{account}" in downloaded.text
        missing = client.get("/report/an_nonexistent", params=params)
        assert missing.status_code == 404
        # The legacy /analyze upload was intentionally disabled before this change.
        disabled = client.post("/analyze", files={"file": ("synthetic.csv", b"date,amount\n2026-09-01,100", "text/csv")})
        assert disabled.status_code == 405
