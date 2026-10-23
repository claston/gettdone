import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_ailos_ocr_columns import ailos_extraction, change
from tests.test_embedded_ocr_quality import layered_pdf

pytestmark = pytest.mark.real_http


def assert_analysis(analysis):
    assert analysis["transactions_total"] == 3
    assert analysis["net_total"] == 40
    assert analysis["opening_balance"] == 1000
    assert analysis["closing_balance"] == 1040
    assert [row["amount"] for row in analysis["preview_transactions"]] == [100, -50, -10]
    assert not any(row["warning_types"] for row in analysis["preview_transactions"])


@pytest.mark.parametrize("mode", ["text", "analysis", "csv"])
def test_bad_hidden_layer_reaches_visual_columns_and_correct_exports(mode, tmp_path, monkeypatch):
    configure_ocr(monkeypatch, ailos_extraction(), mode)
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    raw = layered_pdf(pages=2)
    filename, mime = "ailos.pdf", "application/pdf"
    if mode == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-08-01,PIX,100\n"
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-ailos-layered"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        if mode != "csv":
            assert_analysis(analysis)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx" and mode != "csv":
                assert report.text.count("<STMTTRN>") == 3 and "<TRNAMT>-50.00" in report.text
                assert "<BALAMT>1040.00" in report.text
        assert client.get("/report/an_ailos_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("failure", ["unavailable", "invalid_controls"])
def test_unconfirmed_visual_extraction_never_returns_to_bad_hidden_text(failure, tmp_path, monkeypatch):
    extraction = change(ailos_extraction(), "-50,00", "-50,01")
    configure_ocr(monkeypatch, extraction)
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    if failure == "unavailable":
        monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        response = client.post(
            "/convert",
            data={"anonymous_fingerprint": "fp-ailos-invalid"},
            files={"file": ("ailos.pdf", layered_pdf(pages=2), "application/pdf")},
        )
        if failure == "unavailable":
            assert response.status_code == 400, response.text
            assert "camada de texto" in response.text
        else:
            assert response.status_code == 200, response.text
            analysis = response.json()["analysis"]
            assert [row["amount"] for row in analysis["preview_transactions"]] == [100, -50.01, -10]
            assert "financial_validation_failed" in analysis["preview_transactions"][0]["warning_types"]


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_worker_uses_visual_recovery_despite_existing_native_text(mode, tmp_path, monkeypatch):
    configure_ocr(monkeypatch, ailos_extraction(), mode)
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    with worker_case(tmp_path, layered_pdf(pages=2), filename="ailos.pdf") as (client, job):
        assert_analysis(job["result"]["analysis"])
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200, report.text
        assert report.text.count("<STMTTRN>") == 3 and "<TRNAMT>-50.00" in report.text
