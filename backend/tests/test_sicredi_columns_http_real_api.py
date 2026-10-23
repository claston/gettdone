"""Synthetic native Sicredi PDFs through actual public and worker HTTP flows."""
import hashlib

import httpx
import pytest

from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import (
    get_access_control_service,
    get_analysis_repository,
    get_async_conversion_report_service,
    get_conversion_batch_service,
    get_conversion_processing_pipeline,
    get_report_service,
)
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_sicredi_financial_columns import html_text, matrix_text

pytestmark = pytest.mark.real_http


def document(kind):
    text = matrix_text() if kind == "matrix" else html_text()
    return generate_native_text_pdf((tuple(text.splitlines()),))


def assert_analysis(analysis, kind):
    assert analysis["transactions_total"] == (4 if kind == "matrix" else 8)
    assert analysis["net_total"] == (-4964.91 if kind == "matrix" else -79.94)
    assert analysis["opening_balance"] == (3982.13 if kind == "matrix" else 93.34)
    assert analysis["closing_balance"] == (-982.78 if kind == "matrix" else 13.4)
    assert not any(row["warning_types"] for row in analysis["preview_transactions"])


@pytest.mark.parametrize("kind", ["matrix", "html", "csv"])
def test_public_sicredi_upload_and_reports(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = document(kind)
    filename, mime = "sicredi.pdf", "application/pdf"
    if kind == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-09-01,PIX,100\n"
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-sicredi-synthetic"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        if kind != "csv":
            assert_analysis(analysis, kind)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx" and kind != "csv":
                assert exported.text.count("<STMTTRN>") == (4 if kind == "matrix" else 8)
                assert "<TRNAMT>-250.00" in exported.text if kind == "matrix" else "<TRNAMT>-417.42" in exported.text
        assert client.get("/report/an_sicredi_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("kind", ["matrix", "html"])
def test_sicredi_inconsistent_controls_convert_with_review_over_http(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    text = matrix_text().replace("7.564,91", "7.564,92") if kind == "matrix" else html_text().replace("R$ 91,87", "R$ 91,88")
    raw = generate_native_text_pdf((tuple(text.splitlines()),))

    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-sicredi-invalid"},
                               files={"file": ("sicredi.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == (4 if kind == "matrix" else 8)
        assert "financial_validation_failed" in analysis["preview_transactions"][0]["warning_types"]
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("kind", ["matrix", "html"])
def test_sicredi_worker_preserves_columns_and_period_balances(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = document(kind)

    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage)
        processor = ConversionLambdaProcessor(repository=service.repository, document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage))
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, timeout=20, headers={"Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}) as client:
            created = client.post("/api/conversion-batches", headers={"Idempotency-Key": kind}, json={"files": [
                {"filename": "sicredi.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()},
            ]})
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert client.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id=kind,
                              receive_count=1, lease_owner="sicredi-synthetic-worker")
            job = client.get(f"/api/conversion-batches/{batch_id}").json()["jobs"][0]
            assert job["status"] == "completed", job
            assert_analysis(job["result"]["analysis"], kind)
            report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
            assert report.status_code == 200, report.text
            assert report.text.count("<STMTTRN>") == (4 if kind == "matrix" else 8)
