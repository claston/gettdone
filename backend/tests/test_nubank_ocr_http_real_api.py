"""Actual TCP uploads/worker/downloads with synthetic Nubank Textract evidence."""
import hashlib

import httpx
import pytest

from app.application import pdf_parser
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
from tests.test_nubank_ocr_daily_blocks import change_text, nubank_extraction

pytestmark = pytest.mark.real_http


def configure_ocr(monkeypatch, mode, *, invalid=False):
    extraction = nubank_extraction()
    if invalid:
        extraction = change_text(extraction, "1.234,74", "1.234,75")
    blocks = [{"Id": line.id, "BlockType": "LINE", "Page": page.page_number, "Text": line.text,
        "Confidence": 99, "Geometry": {"BoundingBox": {key.title(): value for key, value in line.bbox.items()}}}
        for page in extraction.pages for line in page.lines]
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "true")
    monkeypatch.setenv("TEXTRACT_TEMP_BUCKET", "synthetic-unused-bucket")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", lambda *a, **kw: {
        "document_hash": "synthetic-nubank", "page_count": 3, "blocks": blocks, "metrics": {"textract_mode": mode},
    })
    return generate_native_text_pdf((("Synthetic OCR source",),) * 3)


def assert_analysis(analysis):
    assert analysis["transactions_total"] == 5
    assert analysis["net_total"] == 2821.35
    assert analysis["opening_balance"] == 0
    assert analysis["closing_balance"] == 2821.35
    rows = analysis["preview_transactions"]
    assert [row["amount"] for row in rows] == [1234.74, 1222.36, 1100, -400, -335.75]
    assert not any(row["warning_types"] for row in rows)


@pytest.mark.parametrize("mode", ["text", "analysis", "csv"])
def test_public_nubank_ocr_upload_and_reports(mode, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, mode)
    filename, mime = "nubank.pdf", "application/pdf"
    if mode == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-09-01,PIX,100\n"
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-nubank-synthetic"}
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
                assert report.text.count("<STMTTRN>") == 5
                assert "<TRNAMT>1234.74" in report.text and "<TRNAMT>1222.36" in report.text
                assert "<BALAMT>2821.35" in report.text
        assert client.get("/report/an_nubank_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_inconsistent_daily_totals_convert_with_review_over_http(tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, "text", invalid=True)

    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-nubank-invalid"},
                               files={"file": ("nubank.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == [1234.75, 1222.36, 1100, -400, -335.75]
        assert "financial_validation_failed" in analysis["preview_transactions"][0]["warning_types"]
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_worker_exports_nubank_receipts_and_correct_balances(mode, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, mode)

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
            created = client.post("/api/conversion-batches", headers={"Idempotency-Key": mode}, json={"files": [
                {"filename": "nubank.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()},
            ]})
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert client.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id=mode,
                              receive_count=1, lease_owner="nubank-synthetic-worker")
            job = client.get(f"/api/conversion-batches/{batch_id}").json()["jobs"][0]
            assert job["status"] == "completed", job
            assert_analysis(job["result"]["analysis"])
            exported = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
            assert exported.status_code == 200, exported.text
            assert exported.text.count("<STMTTRN>") == 5
            assert "<TRNAMT>1234.74" in exported.text and "<TRNAMT>1222.36" in exported.text
