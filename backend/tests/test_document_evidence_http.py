import hashlib

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
from tests.http_runtime import LoopbackClient
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_document_evidence import INVOICE, RECEIPT

pytestmark = pytest.mark.real_http


def test_worker_import_and_manual_correction_keep_evidence_out_of_bank_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    texts = {
        "receipt.pdf": RECEIPT,
        "invoice.pdf": INVOICE,
        "statement.pdf": ("EXTRATO CONTA CORRENTE\nSaldo anterior: 100,00\nDATA HISTORICO VALOR SALDO\n"
                          "20/07/2026 PIX RECEBIDO 50,00 150,00\nSaldo final: 150,00"),
    }
    originals = {name: generate_native_text_pdf((tuple(text.splitlines()),)) for name, text in texts.items()}

    class Originals:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, originals[reference.filename])

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage=storage)
        processor = ConversionLambdaProcessor(
            repository=service.repository,
            document_store=Originals(),
            pipeline=DocumentConversionPipeline(
                report_service=reports,
                access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(),
                analysis_repository=storage,
            ),
        )
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        app.dependency_overrides[get_report_service] = lambda: reports
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with LoopbackClient(base_url=base_url, headers=headers) as client:
            customer = client.post("/api/pro/organization/clients", json={"name": "Cliente de teste"}).json()["id"]
            created = client.post(
                "/api/pro/conversion-batches",
                headers={"Idempotency-Key": "evidence-http"},
                json={
                    "files": [
                        {
                            "filename": name,
                            "content_type": "application/pdf",
                            "size_bytes": len(raw),
                            "sha256_hex": hashlib.sha256(raw).hexdigest(),
                        }
                        for name, raw in originals.items()
                    ]
                },
            )
            assert created.status_code == 201, created.text
            batch = created.json()
            for job in batch["jobs"]:
                organized = client.put(
                    f"/api/pro/organization/batches/{batch['batch_id']}/jobs/{job['job_id']}/context", json={"client_id": customer}
                )
                assert organized.status_code == 200, organized.text
            assert client.post(f"/api/pro/conversion-batches/{batch['batch_id']}/submit", json={}).status_code == 202
            for job_id in publisher.calls:
                processor.process(
                    job_id=job_id, expected_batch_id=batch["batch_id"], trace_id="evidence", receive_count=1, lease_owner="worker"
                )
            jobs = client.get(f"/api/pro/conversion-batches/{batch['batch_id']}").json()["jobs"]
            for job in jobs:
                assert job["status"] == "completed", job
                aid = job["analysis_id"]
                assert client.get(f"/report/{aid}").status_code == 200
                assert client.get(f"/convert-report/{aid}?format=xlsx").status_code == 200
                if job["filename"] != "statement.pdf":
                    analysis = job["result"]["analysis"]
                    assert analysis["financial_document"]["events"] == []
                    assert analysis["transactions_total"] == analysis["net_total"] == 0
                    assert client.get(f"/convert-report/{aid}?format=ofx").status_code == 400
            statement = next(job for job in jobs if job["filename"] == "statement.pdf")
            aid = statement["analysis_id"]
            initial = statement["result"]["analysis"]
            assert initial["transactions_total"] == 1
            command = {
                "edits": [],
                "document_type": "bank_receipt",
                "operation_id": "correction",
                "expected_updated_at": initial["updated_at"],
                "job_id": statement["job_id"],
                "batch_id": batch["batch_id"],
            }
            assert client.post(f"/convert-edits/{aid}", json=command).status_code == 403
            assert client.post(f"/api/pro/convert-edits/{aid}", json={**command, "job_id": "job_foreign"}).status_code == 404
            changed = client.post(f"/api/pro/convert-edits/{aid}", json=command)
            assert changed.status_code == 200, changed.text
            assert changed.json()["net_total"] == 0
            assert changed.json()["document_type_history"][0]["before"] == "bank_statement"
            assert client.post(f"/api/pro/convert-edits/{aid}", json=command).status_code == 200
            assert client.get(f"/convert-report/{aid}?format=ofx").status_code == 400
            assert client.get(f"/convert-report/{aid}?format=xlsx").status_code == 200
            listed = client.get("/api/pro/documents", params={"client_id": customer, "category": "receipts"}).json()["items"]
            assert len(listed) == 2
            restored = client.post(
                f"/api/pro/convert-edits/{aid}",
                json={
                    **command,
                    "document_type": "bank_statement",
                    "operation_id": "restore",
                    "expected_updated_at": changed.json()["updated_at"],
                },
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["net_total"] == initial["net_total"]
            assert client.get(f"/convert-report/{aid}?format=ofx").status_code == 200
            assert client.get("/report/an_evidence_missing").status_code == 404
