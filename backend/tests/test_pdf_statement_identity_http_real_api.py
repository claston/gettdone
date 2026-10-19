import httpx
import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_pdf_statement_identity import statement_page

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["nubank", "multiple_owners", "csv"])
def test_http_statement_owner_identity_conversion_report_and_negative_paths(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    if kind == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-09-01,PIX RECEBIDO,100\n2026-09-02,PIX ENVIADO,-30\n"
    else:
        filename, mime = "statement.pdf", "application/pdf"
        first = statement_page()
        second = statement_page(
            owner_account="456789123-4" if kind == "multiple_owners" else "123456789-0",
            counterparty_agency="3593", counterparty_account="456789-1",
            movement="02/09/2026 PIX ENVIADO FORNECEDOR -30,00 1.070,00",
        )
        raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in (first, second)))

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-statement-owner"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        if kind == "multiple_owners":
            assert response.status_code == 400, response.text
            assert "contas ou períodos" in response.text
            return
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == 2
        assert analysis["net_total"] == 70.0
        assert [row["amount"] for row in analysis["preview_transactions"]] == [100.0, -30.0]
        if kind == "nubank":
            assert analysis["opening_balance"] == 1000.0
            assert analysis["closing_balance"] == 1070.0
            assert [row["source_page"] for row in analysis["preview_transactions"]] == [1, 2]
        report = client.get(f"/report/{analysis['analysis_id']}", params=owner)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        assert client.get("/report/an_owner_nonexistent", params=owner).status_code == 404
        # The supported upload endpoint is /convert; /analyze remains disabled.
        assert client.post("/analyze", files={"file": (filename, raw, mime)}).status_code == 405
