"""Real HTTP conversion, persisted reports and exports retain financial roles."""
import json

import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.http_runtime import LoopbackClient
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_mentore_sipag import mentore_cells, sales_cells

pytestmark = pytest.mark.real_http


def test_cash_and_installments_survive_real_http_storage_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path/"analyses")

    def configure(_):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    cases = [("fixture.csv", "text/csv", b"date,description,amount\n2026-10-01,PIX,100\n", 1),
             ("cash.pdf", "application/pdf", grid_pdf([mentore_cells()]), 4),
             ("sales.pdf", "application/pdf", grid_pdf([sales_cells()]), 3)]
    with _run_http_server(configure) as base, LoopbackClient(base_url=base, timeout=30) as client:
        for filename, mime, raw, count in cases:
            owner = {"anonymous_fingerprint": "fp-mentore-sipag-"+filename}
            response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
            assert response.status_code == 200, response.text
            data = response.json()["analysis"]
            identifier = data["analysis_id"]
            assert data["transactions_total"] == count
            report = client.get("/report/"+identifier, params=owner)
            assert report.status_code == 200, report.text
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get("/convert-report/"+identifier, params={**owner, "format": fmt})
                if filename == "sales.pdf" and fmt == "ofx":
                    assert exported.status_code == 400, exported.text
                else:
                    assert exported.status_code == 200, exported.text
                if filename == "sales.pdf" and fmt == "csv":
                    assert "000123" in exported.text and "Parcela" in exported.text
            if filename == "sales.pdf":
                assert data["total_inflows"] == data["total_outflows"] == data["net_total"] == 0
                saved = json.loads((tmp_path/"analyses"/identifier/"analysis.json").read_text(encoding="utf-8"))
                assert all(event["cash_effect"] is None for event in saved["financial_document"]["events"])
                assert len(saved["financial_document"]["events"]) == 3
        assert client.get("/report/an_missing_installments", params=owner).status_code == 404
        assert client.post("/analyze", data=owner, files={"file": (cases[0][0], cases[0][2], cases[0][1])}).status_code == 405
