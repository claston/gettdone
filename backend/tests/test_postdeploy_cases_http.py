"""Exercise the production upload and worker paths with fictional statements."""

import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_postdeploy_ocr_columns import banpara_columns, bb_signed

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("factory,amounts,bank", [(bb_signed, [50, -20], "001"), (banpara_columns, [-20, .01, 50], "037")])
def test_public_and_worker_preserve_printed_columns_and_downloads(factory, amounts, bank, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, factory())
    with public_client(tmp_path / "public") as client:
        owner = {"anonymous_fingerprint": "fp-postdeploy-columns"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == amounts
        assert analysis["bank_code"] == bank
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        assert client.get("/report/an_nonexistent", params=owner).status_code == 404
        for fmt in ("csv", "ofx"):
            report = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": fmt})
            assert report.status_code == 200
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == len(amounts)
                assert f"<BANKID>{bank}" in report.text
    with worker_case(tmp_path / "worker", raw) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == len(amounts)
        assert analysis["bank_code"] == bank
        report = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200
        assert report.text.count("<STMTTRN>") == len(amounts)
