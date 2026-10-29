import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.received_statements import ReceivedStatements
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_conversion_batch_service,
    get_report_service,
)
from app.main import app
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server


@pytest.mark.real_http
def test_native_pdf_processing_identifies_holder_and_original_report_stays_available(tmp_path, monkeypatch):
    from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
    from app.application.conversion.uploaded_document import ingest_uploaded_document
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.report_service import ReportService
    from app.application.storage_service import TempAnalysisStorage
    from app.workers.conversion_lambda import ConversionLambdaProcessor
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf(((
        "Banco Teste - Extrato de conta corrente", "Titular: Padaria Sao Jose",
        "CPF/CNPJ do titular: 12.345.678/0001-90", "Agencia: 0001 Conta: 12345-6",
        "Periodo: 01/09/2026 a 30/09/2026", "Saldo anterior: R$ 150,00",
        "Sem movimentacao no periodo.", "Saldo final: R$ 150,00",
    ),))

    class Documents:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base, _, token):
        access = app.dependency_overrides[get_access_control_service]()
        batches = app.dependency_overrides[get_conversion_batch_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage=storage)
        app.dependency_overrides[get_report_service] = lambda: reports
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        processor = ConversionLambdaProcessor(repository=batches.repository, document_store=Documents(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage),
            receipt_recorder=ReceivedStatements(access.db).receive)
        with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}",
            "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}) as client:
            response = client.post("/api/pro/conversion-batches", headers={"Idempotency-Key": "holder-pdf"}, json={"files": [
                {"filename": "titular.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()}]})
            assert response.status_code == 201, response.text
            batch_id, job_id = response.json()["batch_id"], response.json()["jobs"][0]["job_id"]
            batches.repository.mark_uploaded(job_id)
            batches.repository.mark_queued(job_id)
            processor.process(job_id=job_id, expected_batch_id=batch_id, trace_id="holder-pdf", receive_count=1, lease_owner="test")
            record = batches.repository.get(job_id)
            assert record.result, record.failure
            source = record.result.payload["organization_source"]
            assert source["holder_tax_id"] == "12345678000190" and source["references"]
            page = client.get("/api/pro/receipts").json()
            assert page["clients"][0]["name"] == "Padaria Sao Jose"
            assert client.get(f"/report/{record.result.analysis_id}").status_code == 200
            assert client.get("/report/nonexistent-ofx62").status_code == 404
            # The retired analyze route remains intentionally disabled, as in main.
            retired = client.post("/analyze", files={"file": ("statement.csv", b"date,amount\n2026-10-01,12\n", "text/csv")})
            assert retired.status_code == 405


@pytest.mark.real_http
def test_receipts_real_http_multi_batch_workspace_export_permissions_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base, publisher, token):
        access = app.dependency_overrides[get_access_control_service]()
        batches = app.dependency_overrides[get_conversion_batch_service]()
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        remaining = access.get_remaining_quota(identity)
        receipts = ReceivedStatements(access.db, batches.repository)
        paths = {}

        class Reports:
            def get_convert_report_path(self, analysis_id, file_format):
                return paths.get(analysis_id)

        app.dependency_overrides[get_report_service] = Reports
        app.dependency_overrides[get_async_conversion_report_service] = Reports
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with httpx.Client(base_url=base, headers=headers) as client:
            jobs = []
            for index in range(3):
                raw = b"date,amount,description\n2026-10-01,12,PIX\n"
                created = client.post("/api/pro/conversion-batches", headers={"Idempotency-Key": f"receipts-{index}"}, json={"files": [
                    {"filename": f"extrato-{index}.csv", "content_type": "text/csv", "size_bytes": len(raw),
                     "sha256_hex": hashlib.sha256(raw).hexdigest()}]})
                assert created.status_code == 201, created.text
                batch_id = created.json()["batch_id"]
                snapshot = batches.repository.get_for_owner(batch_id, identity_type="user", identity_id=identity.identity_id)
                job_id = snapshot.jobs[0].job.job_id
                for method in ("mark_uploaded", "mark_queued", "mark_running"):
                    getattr(batches.repository, method)(job_id)
                path = tmp_path / f"report-{index}.ofx"
                path.write_text(f"OFX-{index}", encoding="utf-8")
                paths[f"an_receipt_{index}"] = path
                batches.repository.mark_completed(job_id, result=ConversionJobResultReference(analysis_id=f"an_receipt_{index}", payload={
                    "organization_source": {"holder_name": "Padaria" if index < 2 else "Outro cliente",
                        "holder_tax_id": "12345678000190" if index < 2 else "11111111000111", "confidence": "strong",
                        "bank_code": "341", "branch": "0001", "account_number": "0001234"},
                    "analysis": {"file_type": "csv", "preview_transactions": []}}))
                receipts.receive(batches.repository.get(job_id))
                jobs.append((batch_id, job_id))
            refreshed = client.post("/api/pro/receipts/refresh")
            assert refreshed.status_code == 200, refreshed.text
            dashboard = client.get("/api/pro/receipts")
            assert dashboard.status_code == 200, dashboard.text
            assert dashboard.json()["totals"] == {"statements": 3, "ready": 3, "attention": 0}
            person = next(c for c in dashboard.json()["clients"] if c["name"] == "Padaria")
            assert person["historical_count"] == 2 and person["accounts"][0]["account_number"] == "••1234"
            workspace = client.get(f"/api/pro/receipts/clients/{person['id']}/workspace")
            assert workspace.status_code == 200, workspace.text
            assert {j["source_batch_id"] for j in workspace.json()["jobs"]} == {jobs[0][0], jobs[1][0]}
            exported = client.get(f"/api/pro/receipts/clients/{person['id']}/export")
            assert exported.status_code == 200, exported.text
            with ZipFile(BytesIO(exported.content)) as archive:
                assert {archive.read(name).decode() for name in archive.namelist()} == {"OFX-0", "OFX-1"}
            changed = client.patch(f"/api/pro/receipts/clients/{person['id']}", json={"name": "Padaria corrigida",
                "tax_id": person["tax_id"], "expected_updated_at": person["updated_at"]})
            assert changed.status_code == 200, changed.text
            assert client.patch(f"/api/pro/receipts/clients/{person['id']}", json={"name": "Stale",
                "tax_id": person["tax_id"], "expected_updated_at": person["updated_at"]}).status_code == 409
            assert httpx.get(base + "/api/pro/receipts").status_code == 401
            foreign = access.register_user(name="Other", email="receipt-foreign@example.test", password="strong-password")
            access.activate_user_plan(user_id=foreign.user_id, plan_code="essencial")
            for suffix in ("workspace", "export"):
                response = client.get(f"/api/pro/receipts/clients/{person['id']}/{suffix}",
                                      headers={"Authorization": f"Bearer {foreign.token}"})
                assert response.status_code == 404
            # Persist the deadline, then simulate cleanup actually deleting the original job.
            expired = replace(batches.repository.get(jobs[0][1]), expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            receipts.receive(expired)
            batches.repository._jobs.pop(jobs[0][1])
            batches.repository._batch_jobs[jobs[0][0]].remove(jobs[0][1])
            refreshed_page = client.get("/api/pro/receipts").json()
            assert refreshed_page["totals"] == {"statements": 2, "ready": 2, "attention": 0}
            person = next(c for c in refreshed_page["clients"] if c["id"] == person["id"])
            assert (person["historical_count"], person["available_count"], person["expired_count"]) == (2, 1, 1)
            active = client.get(f"/api/pro/receipts/clients/{person['id']}/workspace").json()["jobs"]
            assert [item["job_id"] for item in active] == [jobs[1][1]]
            for _, remaining_job in jobs[1:]:
                expired = replace(batches.repository.get(remaining_job), expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
                receipts.receive(expired)
            all_expired = client.get("/api/pro/receipts").json()
            assert all_expired["totals"] == {"statements": 0, "ready": 0, "attention": 0}
            assert sum(item["historical_count"] for item in all_expired["clients"]) == 3
            assert sum(item["expired_count"] for item in all_expired["clients"]) == 3
            monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "false")
            assert client.get("/api/pro/receipts").status_code == 503
            assert client.get("/api/pro/conversions").status_code == 200
            assert publisher.calls == [] and access.get_remaining_quota(identity) == remaining
