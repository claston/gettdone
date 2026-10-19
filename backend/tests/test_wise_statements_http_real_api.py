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
    get_async_conversion_report_service,
    get_conversion_batch_service,
    get_report_service,
)
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_wise_statements import wise_lines

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["wise_brl", "wise_usd_empty", "csv"])
def test_http_wise_worker_review_report_and_export_preserve_balances_and_currency(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind == "csv":
        filename, content_type = "extrato.csv", "text/csv"
        raw = b"date,description,amount\n2026-04-07,CREDITO,40\n2026-04-10,DEBITO,-10\n"
        expected_count, expected_balance, currency = 2, None, None
    else:
        filename, content_type = "wise.pdf", "application/pdf"
        currency = "USD" if kind == "wise_usd_empty" else "BRL"
        lines = wise_lines(currency, empty=currency == "USD", balance="57,25" if currency == "USD" else "1,50")
        raw = generate_native_text_pdf((tuple(lines),))
        expected_count, expected_balance = (0, 57.25) if currency == "USD" else (6, 1.5)

    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage=storage)
        processor = ConversionLambdaProcessor(
            repository=service.repository, document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage),
        )
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        app.dependency_overrides[get_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, headers={
            "Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
        }) as owner:
            created = owner.post("/api/conversion-batches", headers={"Idempotency-Key": kind}, json={
                "files": [{"filename": filename, "content_type": content_type,
                           "size_bytes": len(raw), "sha256_hex": hashlib.sha256(raw).hexdigest()}],
            })
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert owner.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id=kind,
                              receive_count=1, lease_owner="wise-test-worker")
            batch = owner.get(f"/api/conversion-batches/{batch_id}")
            assert batch.status_code == 200, batch.text
            job = batch.json()["jobs"][0]
            assert job["status"] == "completed", job
            analysis = job["result"]["analysis"]
            assert analysis["transactions_total"] == expected_count
            original_regions = analysis["preview_transactions"][0]["source_regions"] if expected_count else []
            if kind == "wise_brl":
                assert original_regions
            if currency:
                assert analysis["closing_balance"] == expected_balance
                assert analysis["pdf_processing_metrics"]["canonical_warning_transactions_count"] == 0
            report = owner.get(f"/report/{job['analysis_id']}")
            assert report.status_code == 200, report.text
            edited = owner.post(f"/convert-edits/{job['analysis_id']}", json={
                "edits": [], "expected_updated_at": analysis["updated_at"], "account_number": "12345678",
            })
            assert edited.status_code == 200, edited.text
            if kind == "wise_brl":
                assert edited.json()["preview_transactions"][0]["source_regions"] == original_regions
                marker = {"page": 1, "left": 0.1, "top": 0.2, "width": 0.5, "height": 0.03}
                marked = owner.post(f"/convert-edits/{job['analysis_id']}", json={
                    "batch_id": batch_id, "job_id": job["job_id"], "expected_updated_at": edited.json()["updated_at"],
                    "edits": [{"row_id": analysis["preview_transactions"][0]["transaction_id"],
                               "action": "annotate", "review_regions": [marker]}],
                })
                assert marked.status_code == 200, marked.text
                reopened = owner.get(f"/api/conversion-batches/{batch_id}").json()
                assert reopened["jobs"][0]["result"]["analysis"]["preview_transactions"][0]["source_regions"] == original_regions
                assert reopened["jobs"][0]["result"]["analysis"]["preview_transactions"][0]["review_regions"] == [marker]
            if currency:
                assert edited.json()["closing_balance"] == expected_balance
            for query in ["", "&account_number=87654321"]:
                exported = owner.get(f"/convert-report/{job['analysis_id']}?format=ofx{query}")
                assert exported.status_code == 200, exported.text
                assert exported.text.count("<STMTTRN>") == expected_count
                if currency:
                    assert f"<CURDEF>{currency}" in exported.text
            missing = owner.get("/report/an_nonexistent")
            assert missing.status_code == 404
