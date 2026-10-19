import httpx
import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_pdf_split_sign_and_balance_labels import bb_statement_lines, flash_statement_lines

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["flash", "bb", "csv"])
def test_http_conversion_and_ofx_export_keep_correct_signs_and_exclude_closing_balance(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    if kind == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-09-01,CREDITO,100\n2026-09-02,DEBITO,-30\n"
        expected = [100.0, -30.0]
    else:
        filename, mime = f"{kind}.pdf", "application/pdf"
        lines = flash_statement_lines() if kind == "flash" else bb_statement_lines()
        raw = generate_native_text_pdf((lines,))
        expected = [-1173.0, 1173.0] if kind == "flash" else [1000.0, -2.55]

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-split-sign-balance"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == 2
        assert [row["amount"] for row in analysis["preview_transactions"]] == expected
        assert analysis["net_total"] == round(sum(expected), 2)
        analysis_id = analysis["analysis_id"]
        report = client.get(f"/report/{analysis_id}", params=owner)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        exported = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200, exported.text
        assert exported.text.count("<STMTTRN>") == 2
        for amount in expected:
            assert f"<TRNAMT>{amount:.2f}" in exported.text
        if kind == "bb":
            assert analysis["closing_balance"] == 1064.15
            assert "<BALAMT>1064.15" in exported.text
        assert client.get("/report/an_split_sign_missing", params=owner).status_code == 404
        # Upload happy paths use /convert; the historical /analyze route is disabled.
        assert client.post("/analyze", files={"file": (filename, raw, mime)}).status_code == 405
