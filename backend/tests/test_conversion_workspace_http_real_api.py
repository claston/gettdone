from __future__ import annotations

import hashlib
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.application.access_control import AccessControlService, IdentityContext
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.s3_direct_upload_service import PreparedS3Upload
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_document_store,
    get_conversion_batch_service,
    get_conversion_runtime_config,
    get_report_service,
)
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("api_prefix", ["/api", "/api/pro"])
@pytest.mark.parametrize("statement_kind", ["explicit", "santander"])
def test_http_empty_statement_completes_in_worker_and_exports(tmp_path: Path, api_prefix: str, statement_kind: str, monkeypatch) -> None:
    from io import BytesIO
    from zipfile import ZipFile

    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
    from tests.fixtures.empty_statements import inactive_santander_pages

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf(((
        "Banco Teste - Extrato de conta corrente",
        "Agência: 0001 Conta: 12345-6",
        "Período: 01/09/2026 a 30/09/2026",
        "Saldo anterior: R$ 150,00",
        "Sem movimentação no período.",
        "Saldo final: R$ 150,00",
    ),))
    if statement_kind == "santander":
        raw = generate_native_text_pdf(inactive_santander_pages())

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
            created = owner.post(f"{api_prefix}/conversion-batches", headers={"Idempotency-Key": "empty-http"}, json={
                "files": [{"filename": "extrato-sem-movimento.pdf", "content_type": "application/pdf",
                    "size_bytes": len(raw), "sha256_hex": hashlib.sha256(raw).hexdigest()}],
            })
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert owner.post(f"{api_prefix}/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id="empty-http",
                              receive_count=1, lease_owner="empty-worker")
            batch = owner.get(f"{api_prefix}/conversion-batches/{batch_id}")
            assert batch.status_code == 200, batch.text
            job = batch.json()["jobs"][0]
            assert job["status"] == "completed", job
            analysis = job["result"]["analysis"]
            assert analysis["transactions_total"] == 0
            assert analysis["preview_transactions"] == []
            assert analysis["total_inflows"] == analysis["total_outflows"] == analysis["net_total"] == 0
            assert analysis["pdf_processing_metrics"]["statement_no_activity"] == 1
            if statement_kind == "explicit":
                assert analysis["opening_balance"] == analysis["closing_balance"] == 150
            else:
                assert analysis["bank_name"] == "Santander" and analysis["bank_code"] == "033"
                assert analysis["opening_balance"] is analysis["closing_balance"] is None
            assert any(insight["type"] == "statement_no_activity" for insight in analysis["insights"])
            # HTML persists account/balance settings before every OFX download.
            settings = owner.post(f"/convert-edits/{job['analysis_id']}", json={
                "edits": [], "expected_updated_at": analysis["updated_at"], "bank_code": "033",
            })
            assert settings.status_code == 200, settings.text
            assert settings.json()["transactions_total"] == 0
            report = owner.get(f"/report/{job['analysis_id']}")
            assert report.status_code == 200, report.text
            ofx = owner.get(f"/convert-report/{job['analysis_id']}?format=ofx")
            assert ofx.status_code == 200, ofx.text
            assert "<BANKTRANLIST>" in ofx.text and "<STMTTRN>" not in ofx.text
            overridden = owner.get(f"/convert-report/{job['analysis_id']}?format=ofx&bank_code=033&account_number=123456")
            assert overridden.status_code == 200, overridden.text
            assert "<BANKID>033" in overridden.text
            assert "<ACCTID>123456" in overridden.text
            if statement_kind == "explicit":
                assert "<BALAMT>150.00" in ofx.text
            else:
                assert "<BANKID>033" in ofx.text
                assert "<BALAMT>" not in ofx.text
            export = owner.get(f"{api_prefix}/conversion-batches/{batch_id}/export")
            assert export.status_code == 200, export.text
            assert export.headers["x-conversion-count"] == "1"
            with ZipFile(BytesIO(export.content)) as archive:
                assert len(archive.namelist()) == 1
                assert b"<STMTTRN>" not in archive.read(archive.namelist()[0])
            missing = owner.get("/convert-report/an_nonexistent?format=ofx")
            assert missing.status_code == 404


def test_http_ofx_batch_preserves_bank_metadata_through_worker_and_export(tmp_path: Path) -> None:
    banks = [("041", "Banrisul"), ("004", "Banco do Nordeste"), ("033", "Santander")]
    originals = {
        f"statement-{index}.ofx": (
            f"<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM>\n"
            f"<BANKID>{code}\n<BRANCHID>0123\n<ACCTID>000456-7\n</BANKACCTFROM>"
            "<BANKTRANLIST><STMTTRN><DTPOSTED>20261001000000\n<TRNAMT>25.00\n"
            "<MEMO>PIX RECEBIDO\n<TRNTYPE>CREDIT\n</STMTTRN></BANKTRANLIST>"
            "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
        ).encode()
        for index, (code, _name) in enumerate(banks)
    }

    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, originals[reference.filename])

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage=storage)
        processor = ConversionLambdaProcessor(
            repository=service.repository,
            document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(
                report_service=reports,
                access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(),
                analysis_repository=storage,
            ),
        )
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        app.dependency_overrides[get_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, headers={
            "Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
        }) as owner:
            created = owner.post("/api/pro/conversion-batches", headers={"Idempotency-Key": "ofx-metadata-http"}, json={
                "files": [{"filename": name, "content_type": "application/x-ofx", "size_bytes": len(raw),
                           "sha256_hex": hashlib.sha256(raw).hexdigest()} for name, raw in originals.items()],
            })
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            submitted = owner.post(f"/api/pro/conversion-batches/{batch_id}/submit", json={})
            assert submitted.status_code == 202, submitted.text
            for job_id in publisher.calls:
                processor.process(job_id=job_id, expected_batch_id=batch_id, trace_id="http-metadata",
                                  receive_count=1, lease_owner=f"http-{job_id}")
            batch = owner.get(f"/api/pro/conversion-batches/{batch_id}")
            assert batch.status_code == 200
            jobs = batch.json()["jobs"]
            assert len(jobs) == 3
            for job, (code, name) in zip(jobs, banks, strict=True):
                assert job["status"] == "completed"
                analysis = job["result"]["analysis"]
                assert analysis["bank_code"] == code
                assert analysis["bank_name"] == name
                assert analysis["file_type"] == "ofx"
                assert analysis["pdf_processing_metrics"] is None
                report = owner.get(f"/report/{job['analysis_id']}")
                assert report.status_code == 200, report.text
                assert report.content.startswith(b"PK")
                exported = owner.get(f"/convert-report/{job['analysis_id']}?format=ofx")
                assert exported.status_code == 200, exported.text
                assert f"<BANKID>{code}" in exported.text
                assert "<ACCTID>0004567" in exported.text
                edited = owner.post(f"/api/pro/convert-edits/{job['analysis_id']}", json={
                    "edits": [{"action": "update", "row_id": "row_1", "date": "2026-10-01",
                               "description": "PIX REVISADO", "credit": 25.0, "debit": None}],
                    "expected_updated_at": analysis["updated_at"],
                })
                assert edited.status_code == 200, edited.text
                exported_after_edit = owner.get(f"/convert-report/{job['analysis_id']}?format=ofx")
                assert f"<BANKID>{code}" in exported_after_edit.text
                assert "<ACCTID>0004567" in exported_after_edit.text
                assert "PIX REVISADO" in exported_after_edit.text
            with httpx.Client(base_url=base_url) as other:
                bootstrap = other.post("/auth/pro/anonymous-session", headers={
                    "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
                }, json={})
                assert bootstrap.status_code == 200
                foreign = other.get(f"/api/pro/conversion-batches/{batch_id}")
                assert foreign.status_code == 404


class FakeAccessControl:
    def resolve_identity(self, *, anonymous_fingerprint, user_token):
        if anonymous_fingerprint != "workspace-owner" or user_token not in {None, ""}:
            return IdentityContext(identity_type="anonymous", identity_id="other", quota_limit=20)
        return IdentityContext(identity_type="anonymous", identity_id="owner", quota_limit=20)


class FakeDocumentStore:
    def load(self, reference):
        return ingest_uploaded_document(reference.filename, b"%PDF-1.4\nworkspace\n")


class FakeReportService:
    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path

    def get_convert_report_path(self, analysis_id: str, file_format: str):
        assert analysis_id == "an_http_workspace"
        assert file_format == "ofx"
        return self.report_path


class NoopUploadService:
    pass


class NoopPublisher:
    def publish(self, **_kwargs):
        return "message"


class PreparedUploadService:
    def __init__(self) -> None:
        self.counter = 0

    def prepare(self, *, filename, content_type, size_bytes, sha256_hex, max_size_bytes):
        self.counter += 1
        document = ConversionDocumentReference(
            storage_key=f"doc_{self.counter:024x}",
            filename=filename,
            file_type=Path(filename).suffix.lstrip("."),
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        )
        return PreparedS3Upload(
            document=document,
            object_key=f"incoming/{document.storage_key}/document.pdf",
            upload_url="https://uploads.example.test",
            upload_fields={"key": document.storage_key, "Content-Type": content_type},
            expires_in_seconds=900,
        )

    def verify_uploaded(self, _document):
        return None


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def publish(self, *, job_id, **_kwargs):
        self.calls.append(job_id)
        return f"message-{job_id}"


def _service() -> tuple[ConversionBatchService, str, str]:
    repository = InMemoryConversionBatchRepository()
    identity = IdentityContext(identity_type="anonymous", identity_id="owner", quota_limit=20)
    batch = ConversionBatch.create(
        identity_type=identity.identity_type,
        identity_id=identity.identity_id,
        files_count=1,
        idempotency_key="http-workspace",
    )
    document = ConversionDocumentReference(
        storage_key="doc_0123456789abcdef01234567",
        filename="extrato-http.pdf",
        file_type="pdf",
        size_bytes=1024,
        sha256_hex="1" * 64,
    )
    job = ConversionJob.create(batch_id=batch.batch_id, document=document, identity=identity)
    repository.create(batch=batch, jobs=[job])
    repository.mark_uploaded(job.job_id)
    repository.mark_queued(job.job_id)
    repository.mark_running(job.job_id)
    repository.mark_completed(
        job.job_id,
        result=ConversionJobResultReference(
            analysis_id="an_http_workspace",
            payload={
                "processing_id": "an_http_workspace",
                "analysis": {"transactions_total": 1, "preview_transactions": []},
            },
        ),
    )
    return (
        ConversionBatchService(
            repository=repository,
            direct_upload_service=NoopUploadService(),
            queue_publisher=NoopPublisher(),
        ),
        batch.batch_id,
        job.job_id,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve_app():
    host = "127.0.0.1"
    port = _find_free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("HTTP test server did not start in time")
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def _run_http_server(tmp_path: Path):
    service, batch_id, job_id = _service()
    report_path = tmp_path / "converted.ofx"
    report_path.write_text("OFXHEADER:100\n", encoding="utf-8")
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControl()
    app.dependency_overrides[get_async_conversion_report_service] = lambda: FakeReportService(report_path)
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: AsyncConversionRolloutPolicy.from_mapping(
        {}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_batch_document_store] = lambda: FakeDocumentStore()
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    try:
        with _serve_app() as base_url:
            yield base_url, batch_id, job_id
    finally:
        app.dependency_overrides.clear()


@contextmanager
def _run_pro_upload_http_server(tmp_path: Path):
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="real-http-test-secret",
    )
    paid_user = access_control.register_user(
        name="Cliente pago",
        email="cliente-pago@example.com",
        password="strong-test-password",
    )
    access_control.activate_user_plan(user_id=paid_user.user_id, plan_code="essencial")
    publisher = RecordingPublisher()
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=PreparedUploadService(),
        queue_publisher=publisher,
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: AsyncConversionRolloutPolicy.from_mapping(
        {}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    try:
        with _serve_app() as base_url:
            yield base_url, publisher, paid_user.token
    finally:
        app.dependency_overrides.clear()
        access_control.close()


def test_http_pro_workspace_batch_document_review_export_and_negative_owner(tmp_path: Path) -> None:
    with _run_http_server(tmp_path) as (base_url, batch_id, job_id):
        params = {"anonymous_fingerprint": "workspace-owner"}
        batch = httpx.get(f"{base_url}/api/pro/conversion-batches/{batch_id}", params=params)
        document = httpx.get(
            f"{base_url}/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/document",
            params=params,
        )
        reviewed = httpx.post(
            f"{base_url}/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/review",
            headers={
                "Origin": "http://localhost:3000",
                "X-OFX-Pro-Client": "web",
            },
            json=params,
        )
        exported = httpx.get(f"{base_url}/api/pro/conversion-batches/{batch_id}/export", params=params)
        forbidden = httpx.get(
            f"{base_url}/api/pro/conversion-batches/{batch_id}",
            params={"anonymous_fingerprint": "not-owner"},
        )

    assert batch.status_code == 200
    assert document.status_code == 200 and document.content.startswith(b"%PDF")
    assert reviewed.status_code == 200 and reviewed.json()["reviewed_at"]
    assert exported.status_code == 200 and exported.content.startswith(b"PK")
    assert forbidden.status_code == 404


def test_http_paid_user_can_create_submit_read_and_rejects_negative_owner(tmp_path: Path) -> None:
    pro_headers = {
        "Origin": "http://localhost:3000",
        "X-OFX-Pro-Client": "web",
    }
    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, paid_user_token):
        with httpx.Client(base_url=base_url) as owner:
            authenticated_headers = {
                **pro_headers,
                "Authorization": f"Bearer {paid_user_token}",
            }
            runtime = owner.get("/api/conversion-runtime", headers=authenticated_headers)
            created = owner.post(
                "/api/pro/conversion-batches",
                headers={**authenticated_headers, "Idempotency-Key": "real-http-upload"},
                json={
                    "files": [
                        {
                            "filename": "extrato.pdf",
                            "content_type": "application/pdf",
                            "size_bytes": 1024,
                            "sha256_hex": "1" * 64,
                        }
                    ]
                },
            )
            batch_id = created.json()["batch_id"]
            submitted = owner.post(
                f"/api/pro/conversion-batches/{batch_id}/submit",
                headers=authenticated_headers,
                json={},
            )
            reloaded = owner.get(
                f"/api/pro/conversion-batches/{batch_id}",
                headers=authenticated_headers,
            )

        with httpx.Client(base_url=base_url) as other:
            other_bootstrap = other.post("/auth/pro/anonymous-session", headers=pro_headers, json={})
            forbidden = other.get(f"/api/pro/conversion-batches/{batch_id}")

    assert runtime.json()["execution_mode"] == "sqs_lambda"
    assert created.status_code == 201 and len(created.json()["uploads"]) == 1
    assert submitted.status_code == 202 and submitted.json()["status"] == "queued"
    assert reloaded.status_code == 200 and reloaded.json()["batch_id"] == batch_id
    assert other_bootstrap.status_code == 200
    assert forbidden.status_code == 404
    assert len(publisher.calls) == 1
