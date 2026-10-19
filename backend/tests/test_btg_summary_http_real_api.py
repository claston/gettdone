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
from tests.test_btg_summary_statement import AMOUNTS_DESCENDING, LAYOUT, btg_summary_pages
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["btg", "btg_incomplete", "csv"])
def test_http_btg_worker_review_report_and_ofx_keep_validated_movements(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-06-04,CREDITO,10\n2026-06-05,DEBITO,-4\n"
        expected = [10.0, -4.0]
    else:
        filename, mime = "btg.pdf", "application/pdf"
        pages = btg_summary_pages()
        if kind == "btg_incomplete":
            pages = pages[1:]
        raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages))
        expected = list(reversed(AMOUNTS_DESCENDING))

    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage)
        processor = ConversionLambdaProcessor(
            repository=service.repository, document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage),
        )
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        app.dependency_overrides[get_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, timeout=20, headers={
            "Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
        }) as client:
            created = client.post("/api/conversion-batches", headers={"Idempotency-Key": kind}, json={
                "files": [{"filename": filename, "content_type": mime, "size_bytes": len(raw),
                           "sha256_hex": hashlib.sha256(raw).hexdigest()}],
            })
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert client.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id=kind,
                              receive_count=1, lease_owner="btg-test-worker")
            response = client.get(f"/api/conversion-batches/{batch_id}")
            assert response.status_code == 200, response.text
            job = response.json()["jobs"][0]
            if kind == "btg_incomplete":
                assert job["status"] == "failed", job
            else:
                assert job["status"] == "completed", job
                analysis = job["result"]["analysis"]
                assert analysis["transactions_total"] == len(expected)
                assert [row["amount"] for row in analysis["preview_transactions"]] == expected
                assert analysis["net_total"] == round(sum(expected), 2)
                if kind == "btg":
                    assert analysis["layout_inference_name"] == LAYOUT
                    assert analysis["opening_balance"] == analysis["closing_balance"] == 0
                    assert analysis["total_inflows"] == 155.0 and analysis["total_outflows"] == -155.0
                    assert analysis["pdf_processing_metrics"]["canonical_warning_transactions_count"] == 0
                    assert analysis["pdf_processing_metrics"]["confidence_band"] == "high"
                report = client.get(f"/report/{job['analysis_id']}")
                assert report.status_code == 200 and report.content.startswith(b"PK")
                exported = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
                assert exported.status_code == 200, exported.text
                assert exported.text.count("<STMTTRN>") == len(expected)
                for amount in expected:
                    assert f"<TRNAMT>{amount:.2f}" in exported.text
                if kind == "btg":
                    assert "<BALAMT>0.00" in exported.text
            assert client.get("/report/an_btg_missing").status_code == 404
