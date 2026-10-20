import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_santander_ocr_controls import change, santander_extraction

pytestmark = pytest.mark.real_http


def assert_analysis(analysis, *, damaged):
    assert analysis["transactions_total"] == 7
    assert analysis["net_total"] == 0
    assert analysis["total_inflows"] == 1634.72
    assert analysis["total_outflows"] == -1634.72
    assert analysis["opening_balance"] == analysis["closing_balance"] == 0
    assert [row["amount"] for row in analysis["preview_transactions"]] == [-100, -.16, 100.16, 1234.56, -1234.56, 300, -300]
    warned = [row for row in analysis["preview_transactions"] if row["warning_types"]]
    assert len(warned) == (2 if damaged else 0)
    if damaged:
        assert all("ocr_amount_recovered_from_summary" in row["warning_types"] for row in warned)
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("mode,damaged", [("text", False), ("text", True), ("analysis", True), ("csv", False)])
def test_public_santander_columns_controls_review_and_all_exports(mode, damaged, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, santander_extraction(damaged=damaged), mode)
    filename, mime = "santander.pdf", "application/pdf"
    if mode == "csv":
        filename, mime, raw = "statement.csv", "text/csv", b"date,description,amount\n2026-07-01,PIX,100\n"
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-santander-columns"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        if mode != "csv":
            assert_analysis(analysis, damaged=damaged)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx" and mode != "csv":
                assert report.text.count("<STMTTRN>") == 7 and "<TRNAMT>-0.16" in report.text
                assert "<BALAMT>0.00" in report.text
        assert client.get("/report/an_santander_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_public_ambiguous_summary_is_refused_instead_of_exporting_a_partial_ledger(tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, change(santander_extraction(damaged=True), "1234,56", "2234,56"))
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-santander-invalid"},
                               files={"file": ("santander.pdf", raw, "application/pdf")})
        assert response.status_code == 400, response.text
        assert "Santander" in response.text


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_worker_preserves_recovered_amounts_and_review_warnings(mode, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, santander_extraction(damaged=True), mode)
    with worker_case(tmp_path, raw, filename="santander.pdf") as (client, job):
        assert_analysis(job["result"]["analysis"], damaged=True)
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200, report.text
        assert report.text.count("<STMTTRN>") == 7 and "<TRNAMT>-0.16" in report.text
