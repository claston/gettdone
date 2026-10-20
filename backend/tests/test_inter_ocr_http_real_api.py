import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_inter_ocr_ledger import change, inter_extraction

pytestmark = pytest.mark.real_http


def assert_analysis(analysis):
    assert analysis["transactions_total"] == 5
    assert analysis["opening_balance"] == 1358.83
    assert analysis["closing_balance"] == 1111.84
    assert analysis["net_total"] == -246.99
    assert [row["amount"] for row in analysis["preview_transactions"]] == [-29.98, -50, 200, -100, -267.01]
    assert not any(row["warning_types"] for row in analysis["preview_transactions"])


@pytest.mark.parametrize("mode", ["text", "analysis", "csv"])
def test_inter_http_upload_and_reports_preserve_all_movements(mode, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, inter_extraction(), mode)
    filename, mime = "inter.pdf", "application/pdf"
    if mode == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-08-01,PIX,100\n"
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint":"fp-inter-synthetic"}
        response = client.post("/convert", data=owner, files={"file":(filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        if mode != "csv":
            assert_analysis(analysis)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis_id}", params={**owner, "format":fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx" and mode != "csv":
                assert report.text.count("<STMTTRN>") == 5
                assert "<TRNAMT>-100.00" in report.text and "<BALAMT>1111.84" in report.text
        assert client.get("/report/an_inter_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_inter_ambiguous_ocr_http_refuses_a_wrong_cell(tmp_path, monkeypatch):
    extraction = change(inter_extraction(), "R$ 1.478,85", "R$ 1.478,86")
    raw = configure_ocr(monkeypatch, extraction)
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint":"fp-inter-invalid"},
                               files={"file":("inter.pdf", raw, "application/pdf")})
        assert response.status_code == 400, response.text
        assert "Banco Inter" in response.text


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_inter_worker_keeps_page_continuation_and_validated_period(mode, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, inter_extraction(), mode)
    with worker_case(tmp_path, raw, filename="inter.pdf") as (client, job):
        assert_analysis(job["result"]["analysis"])
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format":"ofx"})
        assert report.status_code == 200, report.text
        assert report.text.count("<STMTTRN>") == 5 and "<TRNAMT>-100.00" in report.text
