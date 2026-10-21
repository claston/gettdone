"""Verify document routing and signed ledgers over a real local HTTP server."""
import httpx
import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_pdf_audit_oct21_variants import BB_SPLIT, TOTALBANK

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["csv", "bb_split", "totalbank", "receipt"])
def test_audit_documents_over_http(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    text = {"bb_split": BB_SPLIT, "totalbank": TOTALBANK,
            "receipt": "BANCO DO BRASIL\nCOMPROVANTE DE PAGAMENTO DE TITULOS\nVALOR COBRADO 101,20\nDATA DO PAGAMENTO 02/09/2026"}
    raw = (b"date,description,amount\n2026-09-01,CREDITO,100\n2026-09-02,DEBITO,-30\n" if kind == "csv"
           else generate_native_text_pdf((text[kind].splitlines(),)))
    filename = "input.csv" if kind == "csv" else "input.pdf"
    mime = "text/csv" if kind == "csv" else "application/pdf"
    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-audit-http"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        if kind == "receipt":
            assert response.status_code == 400, response.text
            assert "comprovante de pagamento" in response.text
            assert response.json()["detail"]["document_type"] == "bank_receipt"
            return
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == (4 if kind == "bb_split" else 2)
        assert analysis["net_total"] == {"csv": 70, "bb_split": 0, "totalbank": 20}[kind]
        report = client.get(f"/report/{analysis['analysis_id']}", params=owner)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200
        assert exported.text.count("<STMTTRN>") == analysis["transactions_total"]
        assert client.get("/report/an_audit_missing", params=owner).status_code == 404
        # Current production intentionally disables this historical route.
        assert client.post("/analyze", files={"file": (filename, raw, mime)}).status_code == 405
