"""Both standard entry points use the same ledger checks and preserve OFX movements."""

import hashlib
import re

import pytest

from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_access_control_service, get_async_conversion_report_service, get_conversion_batch_service
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.http_runtime import LoopbackClient
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_stone_balance_pair_order import ledger, statement_pages

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("credit_first", [False, True])
def test_free_and_pro_standard_routes_keep_the_same_stone_pairs(tmp_path, monkeypatch, credit_first):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(statement_pages(credit_first))

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
        results = []
        with LoopbackClient(base_url=base_url, timeout=20, headers={"Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}) as client:
            for path in ("/api/conversion-batches", "/api/pro/conversion-batches"):
                created = client.post(path, headers={"Idempotency-Key": path}, json={"files": [{
                    "filename": "stone-fictional.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                    "sha256_hex": hashlib.sha256(raw).hexdigest(),
                }]})
                assert created.status_code == 201, created.text
                batch_id = created.json()["batch_id"]
                assert client.post(f"{path}/{batch_id}/submit", json={}).status_code == 202
                processor.process(job_id=publisher.calls[-1], expected_batch_id=batch_id, trace_id="stone-pairs",
                                  receive_count=1, lease_owner="fictional-worker")
                job = client.get(f"{path}/{batch_id}").json()["jobs"][0]
                assert job["status"] == "completed", job
                analysis = job["result"]["analysis"]
                assert analysis["transactions_total"] == 7
                assert analysis["pdf_processing_metrics"]["balance_consistency_checked"] == 3
                assert analysis["pdf_processing_metrics"]["balance_consistency_failed"] == 0
                assert analysis["review_guidance"]["category"] != "financial_discrepancy"
                fields = ("date", "description", "amount", "running_balance", "source_page", "warning_types")
                results.append([{key: row.get(key) for key in fields} for row in analysis["preview_transactions"]])
                exported = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
                assert exported.status_code == 200, exported.text
                assert exported.text.count("<STMTTRN>") == 7
                assert sorted(map(float, re.findall(r"<TRNAMT>([^<\r\n]+)", exported.text))) == sorted(
                    row.amount for row in ledger(credit_first))
            assert results[0] == results[1]
