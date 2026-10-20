"""Shared real HTTP fixtures for synthetic OCR conversion regressions."""
import hashlib
from contextlib import contextmanager

import httpx

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


def configure_ocr(monkeypatch, extraction, mode="text"):
    blocks = [{"Id": line.id, "BlockType": "LINE", "Page": page.page_number, "Text": line.text,
        "Confidence": line.confidence if line.confidence is not None else 99,
        "Geometry": {"BoundingBox": {key.title(): value for key, value in (line.bbox or {}).items()}}}
        for page in extraction.pages for line in page.lines]
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "true")
    monkeypatch.setenv("TEXTRACT_TEMP_BUCKET", "synthetic-unused-bucket")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", lambda *a, **kw: {
        "document_hash": extraction.document_hash, "page_count": len(extraction.pages),
        "blocks": blocks, "metrics": {"textract_mode": mode},
    })
    return generate_native_text_pdf((("Synthetic OCR source",),) * len(extraction.pages))


@contextmanager
def public_client(tmp_path):
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        yield client


@contextmanager
def worker_case(tmp_path, raw, *, filename="synthetic.pdf"):
    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "worker-analyses", ttl_seconds=3600)
        reports = ReportService(storage)
        processor = ConversionLambdaProcessor(repository=service.repository, document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage))
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, timeout=20, headers={"Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}) as client:
            created = client.post("/api/conversion-batches", headers={"Idempotency-Key": "synthetic-ocr"}, json={"files": [
                {"filename": filename, "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()},
            ]})
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert client.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id="synthetic-ocr",
                              receive_count=1, lease_owner="synthetic-ocr-worker")
            job = client.get(f"/api/conversion-batches/{batch_id}").json()["jobs"][0]
            assert job["status"] == "completed", job
            yield client, job
