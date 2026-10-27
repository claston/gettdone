"""The new document states must survive real upload, report and export requests."""
import pytest

from tests.ocr_conversion_http_helpers import public_client
from tests.test_oct27_today_document_states import bradesco_inactive, xp_inactive
from tests.test_oct27_today_extraction import banestes_page

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("factory,count,bounds", [(banestes_page, 2, (None, None)),
    (xp_inactive, 0, (100, 100)), (bradesco_inactive, 0, (None, None))])
def test_new_ledger_states_preserve_balances_and_empty_exports_over_http(tmp_path, monkeypatch, factory, count, bounds):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-oct27-today-owner"}
        response = client.post("/convert", data=owner, files={"file": ("fictional.pdf", factory(), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        assert (analysis["opening_balance"], analysis["closing_balance"]) == bounds
        if factory is banestes_page:
            assert analysis["bank_code"] == "021"
        identifier = analysis["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx"):
            assert client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt}).status_code == 200
        ofx = client.get(f"/convert-report/{identifier}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert ofx.text.count("<STMTTRN>") == count
        if bounds[1] is None:
            assert "<LEDGERBAL>" not in ofx.text
        else:
            assert f"<BALAMT>{bounds[1]:.2f}" in ofx.text
        assert client.get("/report/an_nonexistent_oct27_today", params=owner).status_code == 404
