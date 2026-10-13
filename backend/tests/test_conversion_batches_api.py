from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dependencies
from app.application.access_control import IdentityContext, RegisteredUser
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchFile, ConversionBatchService
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job_repository import ConversionJobResultReference
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.conversion.s3_direct_upload_service import PreparedS3Upload
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_service,
    get_conversion_document_store,
    get_conversion_runtime_config,
)
from app.main import app
from app.routers.auth_session import ANONYMOUS_IDENTITY_COOKIE_NAME
from app.routers.conversion_batches import get_runtime_access_control_service


class FakeAccessControlService:
    def ensure_quota_available(self, identity, *, required_units=1):
        assert identity.identity_id == "anon_123"
        assert required_units == 2

    def resolve_identity(self, *, anonymous_fingerprint, user_token):
        assert anonymous_fingerprint == "browser-fingerprint"
        assert user_token in {None, ""}
        return IdentityContext(
            identity_type="anonymous",
            identity_id="anon_123",
            quota_limit=20,
            max_upload_size_bytes=5 * 1024 * 1024,
        )

    def decode_anonymous_identity_token(self, *, token: str) -> str:
        assert token == "anonymous-cookie"
        return "browser-fingerprint"


class FakeRegisteredAccessControlService:
    def __init__(
        self,
        *,
        email: str,
        batch_conversion_enabled: bool = True,
        batch_max_files: int = 12,
    ) -> None:
        self.email = email
        self.batch_conversion_enabled = batch_conversion_enabled
        self.batch_max_files = batch_max_files

    def ensure_quota_available(self, identity, *, required_units=1):
        assert identity.identity_id == "usr_canary"
        assert required_units >= 1

    def resolve_identity(self, *, anonymous_fingerprint, user_token):
        assert anonymous_fingerprint in {None, ""}
        assert user_token == "allowed-token"
        return IdentityContext(
            identity_type="user",
            identity_id="usr_canary",
            quota_limit=20,
            max_upload_size_bytes=5 * 1024 * 1024,
            batch_conversion_enabled=self.batch_conversion_enabled,
            batch_max_files=self.batch_max_files,
        )

    def get_user_by_id(self, user_id: str) -> RegisteredUser:
        assert user_id == "usr_canary"
        return RegisteredUser(
            user_id=user_id,
            email=self.email,
            name="Canary",
            token="allowed-token",
        )


class FakeDirectUploadService:
    def __init__(self) -> None:
        self.counter = 0

    def prepare(self, *, filename, content_type, size_bytes, sha256_hex, max_size_bytes):
        self.counter += 1
        document = ConversionDocumentReference(
            storage_key=f"doc_{self.counter:024x}",
            filename=filename,
            file_type="pdf",
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        )
        return self.prepare_reference(document=document, content_type=content_type, max_size_bytes=max_size_bytes)

    def prepare_reference(self, *, document, content_type, max_size_bytes):
        return PreparedS3Upload(
            document=document,
            object_key=f"incoming/{document.storage_key}/document.pdf",
            upload_url="https://uploads.example.test",
            upload_fields={"key": document.storage_key, "Content-Type": content_type},
            expires_in_seconds=900,
        )

    def verify_uploaded(self, document):
        return None


class FakeDocumentStore:
    def load(self, reference):
        return ingest_uploaded_document(
            reference.filename,
            b"%PDF-1.4\n% retained statement\n",
        )


class FakeBatchReportService:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def get_convert_report_path(self, analysis_id: str, file_format: str):
        path = self.root_dir / f"{analysis_id}.{file_format}"
        if not path.exists():
            raise AssertionError(f"missing test report: {path}")
        return path


@dataclass
class FakePublisher:
    calls: list[str]

    def publish(self, *, job_id, batch_id, trace_id):
        self.calls.append(job_id)
        return f"message-{job_id}"


def _request_payload(count: int = 2) -> dict:
    return {
        "anonymous_fingerprint": "browser-fingerprint",
        "files": [
            {
                "filename": f"extrato-{index}.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
                "sha256_hex": f"{index:064x}",
            }
            for index in range(1, count + 1)
        ],
    }


def _completed_batch_service() -> tuple[ConversionBatchService, str, str]:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    identity = FakeAccessControlService().resolve_identity(
        anonymous_fingerprint="browser-fingerprint",
        user_token=None,
    )
    prepared = service.create(
        identity=identity,
        files=[
            ConversionBatchFile(
                filename="extrato.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                sha256_hex="1" * 64,
            )
        ],
        idempotency_key="workspace-test",
    )
    job_id = prepared.snapshot.jobs[0].job.job_id
    service.repository.mark_uploaded(job_id)
    service.repository.mark_queued(job_id)
    service.repository.mark_running(job_id)
    service.repository.mark_completed(
        job_id,
        result=ConversionJobResultReference(
            analysis_id="an_workspace",
            payload={
                "processing_id": "an_workspace",
                "quota_limit": 20,
                "quota_remaining": 19,
                "analysis": {
                    "transactions_total": 1,
                    "bank_name": "Banco Teste",
                    "preview_transactions": [
                        {
                            "date": "2026-10-01",
                            "description": "PIX TESTE",
                            "amount": 25.0,
                            "running_balance": 125.0,
                            "warning_types": [],
                        }
                    ],
                },
            },
        ),
    )
    return service, prepared.snapshot.batch.batch_id, job_id


def test_async_batch_api_creates_submits_and_returns_owner_scoped_status() -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="paid@example.com"
    )
    client = TestClient(app)
    try:
        created = client.post(
            "/api/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "request-123",
                "X-Request-ID": "trace-123",
            },
            json={"files": _request_payload()["files"]},
        )
        assert created.status_code == 201
        created_payload = created.json()
        assert created_payload["status"] == "uploading"
        assert len(created_payload["uploads"]) == 2
        assert created_payload["uploads"][0]["url"].startswith("https://")
        batch_id = created_payload["batch_id"]

        submitted = client.post(
            f"/api/conversion-batches/{batch_id}/submit",
            headers={
                "Authorization": "Bearer allowed-token",
                "X-Request-ID": "trace-456",
            },
            json={},
        )
        assert submitted.status_code == 202
        assert submitted.json()["status"] == "queued"
        assert [item["status"] for item in submitted.json()["jobs"]] == ["queued", "queued"]

        status = client.get(
            f"/api/conversion-batches/{batch_id}",
            headers={"Authorization": "Bearer allowed-token"},
        )
        assert status.status_code == 200
        assert status.json()["batch_id"] == batch_id
    finally:
        app.dependency_overrides.clear()


def test_pro_batch_api_requires_browser_guard_and_preserves_worker_submission(monkeypatch) -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="canary@example.com"
    )
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://pro.example.test")
    client = TestClient(app)
    payload = _request_payload()
    payload.pop("anonymous_fingerprint")
    try:
        missing_guard = client.post(
            "/api/pro/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "request-pro-missing",
            },
            json=payload,
        )
        created = client.post(
            "/api/pro/conversion-batches",
            headers={
                "Idempotency-Key": "request-pro-ok",
                "Authorization": "Bearer allowed-token",
                "Origin": "https://pro.example.test",
                "X-OFX-Pro-Client": "web",
            },
            json=payload,
        )
        batch_id = created.json()["batch_id"]
        submitted = client.post(
            f"/api/pro/conversion-batches/{batch_id}/submit",
            headers={
                "Origin": "https://pro.example.test",
                "X-OFX-Pro-Client": "web",
                "Authorization": "Bearer allowed-token",
            },
            json={},
        )

        assert missing_guard.status_code == 403
        assert created.status_code == 201
        assert submitted.status_code == 202
        assert submitted.json()["status"] == "queued"
        assert len(service.queue_publisher.calls) == 2
    finally:
        app.dependency_overrides.clear()


def test_pro_batch_api_denies_anonymous_identity_even_when_worker_is_globally_enabled(
    monkeypatch,
) -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://pro.example.test")
    client = TestClient(app)
    client.cookies.set(ANONYMOUS_IDENTITY_COOKIE_NAME, "anonymous-cookie")
    payload = _request_payload(1)
    payload.pop("anonymous_fingerprint")
    try:
        response = client.post(
            "/api/pro/conversion-batches",
            headers={
                "Idempotency-Key": "anonymous-pro-request",
                "Origin": "https://pro.example.test",
                "X-OFX-Pro-Client": "web",
            },
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "batch_plan_required"
    assert service.direct_upload_service.counter == 0


def test_paid_plan_batch_limit_is_enforced_before_upload_preparation(monkeypatch) -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="paid@example.com",
        batch_max_files=2,
    )
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://pro.example.test")
    client = TestClient(app)
    try:
        response = client.post(
            "/api/pro/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "paid-over-limit",
                "Origin": "https://pro.example.test",
                "X-OFX-Pro-Client": "web",
            },
            json={"files": _request_payload(3)["files"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "batch_file_limit_exceeded",
        "message": "Este plano permite no máximo 2 arquivos por lote.",
        "batch_max_files": 2,
    }
    assert service.direct_upload_service.counter == 0


def test_pro_batch_submit_rechecks_plan_entitlement(monkeypatch) -> None:
    publisher = FakePublisher(calls=[])
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=publisher,
    )
    access_control = FakeRegisteredAccessControlService(email="paid@example.com")
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://pro.example.test")
    headers = {
        "Authorization": "Bearer allowed-token",
        "Origin": "https://pro.example.test",
        "X-OFX-Pro-Client": "web",
    }
    client = TestClient(app)
    try:
        created = client.post(
            "/api/pro/conversion-batches",
            headers={**headers, "Idempotency-Key": "revoked-before-submit"},
            json={"files": _request_payload(2)["files"]},
        )
        assert created.status_code == 201

        access_control.batch_conversion_enabled = False
        submitted = client.post(
            f"/api/pro/conversion-batches/{created.json()['batch_id']}/submit",
            headers=headers,
            json={},
        )
    finally:
        app.dependency_overrides.clear()

    assert submitted.status_code == 403
    assert submitted.json()["detail"]["code"] == "batch_plan_required"
    assert publisher.calls == []


def test_generic_worker_route_denies_multiple_files_without_batch_entitlement() -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="free@example.com",
        batch_conversion_enabled=False,
        batch_max_files=1,
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "free-multi-file",
            },
            json={"files": _request_payload(2)["files"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "batch_plan_required"
    assert service.direct_upload_service.counter == 0


def test_batch_api_stays_disabled_in_legacy_fallback_mode() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        response = client.post(
            "/api/conversion-batches",
            headers={"Idempotency-Key": "request-123"},
            json=_request_payload(1),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "async_conversion_disabled"
    assert response.json()["detail"]["fallback_endpoint"] == "/api/conversions/upload"


def test_conversion_runtime_endpoint_exposes_safe_fallback_contract() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_access_control_service] = lambda: pytest.fail(
        "legacy runtime must not initialize access control"
    )
    client = TestClient(app)
    try:
        response = client.get("/api/conversion-runtime")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "architecture_mode": "legacy",
        "upload_mode": "proxy",
        "execution_mode": "inline_legacy",
        "batch_max_files": 12,
        "direct_batch_enabled": False,
        "fallback_endpoint": "/api/conversions/upload",
    }


def test_conversion_runtime_uses_access_control_only_for_identity_scoped_rollout() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {
                "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "true",
                "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "100",
            }
        )
    )
    app.dependency_overrides[get_runtime_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    client.cookies.set(ANONYMOUS_IDENTITY_COOKIE_NAME, "anonymous-cookie")
    try:
        response = client.get("/api/conversion-runtime")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["direct_batch_enabled"] is True


def test_percentage_rollout_includes_anonymous_traffic_with_one_file_limit() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_runtime_access_control_service] = lambda: FakeAccessControlService()
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {
                "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "true",
                "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "100",
            }
        )
    )
    client = TestClient(app)
    client.cookies.set(ANONYMOUS_IDENTITY_COOKIE_NAME, "anonymous-cookie")
    try:
        response = client.get("/api/conversion-runtime")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["direct_batch_enabled"] is True
    assert response.json()["batch_max_files"] == 1


def test_allowlisted_user_gets_async_runtime_while_global_mode_stays_legacy() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_runtime_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="a@a.com.br"
    )
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br"}
        )
    )
    client = TestClient(app)
    try:
        allowed = client.get(
            "/api/conversion-runtime",
            headers={"Authorization": "Bearer allowed-token"},
        )
        anonymous = client.get("/api/conversion-runtime")
    finally:
        app.dependency_overrides.clear()

    assert allowed.status_code == 200
    assert allowed.json() == {
        "architecture_mode": "async_aws",
        "upload_mode": "direct_s3",
        "execution_mode": "sqs_lambda",
        "batch_max_files": 12,
        "direct_batch_enabled": True,
        "fallback_endpoint": "/api/conversions/upload",
    }
    assert anonymous.status_code == 200
    assert anonymous.json()["architecture_mode"] == "legacy"
    assert anonymous.json()["direct_batch_enabled"] is False


def test_allowlisted_user_can_create_batch_while_global_mode_stays_legacy() -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="a@a.com.br"
    )
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br"}
        )
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "allowed-request",
            },
            json={"files": _request_payload(1)["files"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["status"] == "uploading"


def test_percentage_user_is_limited_to_one_file_while_global_mode_stays_legacy() -> None:
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="other@example.com"
    )
    app.dependency_overrides[get_runtime_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="other@example.com"
    )
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {
                "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "true",
                "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "100",
            }
        )
    )
    client = TestClient(app)
    try:
        runtime = client.get(
            "/api/conversion-runtime",
            headers={"Authorization": "Bearer allowed-token"},
        )
        response = client.post(
            "/api/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "percentage-request",
            },
            json={"files": _request_payload(2)["files"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert runtime.status_code == 200
    assert runtime.json()["direct_batch_enabled"] is True
    assert runtime.json()["batch_max_files"] == 1
    assert response.status_code == 400
    assert response.json()["detail"] == "Percentage rollout conversions accept only one file per batch."


def test_non_allowlisted_user_cannot_create_batch_in_legacy_mode() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_conversion_batch_service] = lambda: object()
    app.dependency_overrides[get_access_control_service] = lambda: FakeRegisteredAccessControlService(
        email="other@example.com"
    )
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: (
        AsyncConversionRolloutPolicy.from_mapping(
            {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br"}
        )
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/conversion-batches",
            headers={
                "Authorization": "Bearer allowed-token",
                "Idempotency-Key": "blocked-request",
            },
            json={"files": _request_payload(1)["files"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "async_conversion_disabled"


def test_inline_shared_fallback_builds_without_sqs_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CONVERSION_ARCHITECTURE_MODE", "async_aws")
    monkeypatch.setenv("CONVERSION_UPLOAD_MODE", "proxy")
    monkeypatch.setenv("CONVERSION_EXECUTION_MODE", "inline_shared")
    monkeypatch.setenv("CONVERSION_BATCH_REPOSITORY", "memory")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "private-conversions")
    monkeypatch.delenv("CONVERSION_SQS_QUEUE_URL", raising=False)
    monkeypatch.setattr(dependencies, "_conversion_batch_repository", None)
    monkeypatch.setattr(dependencies, "_conversion_batch_service", None)

    service = dependencies.get_conversion_batch_service()

    assert service is not None
    with pytest.raises(RuntimeError, match="disabled by the active runtime fallback"):
        service.queue_publisher.publish(job_id="job_test", batch_id="batch_test", trace_id="trace-test")


@pytest.mark.parametrize(
    "document_path",
    [
        "/api/conversion-batches/{batch_id}/jobs/{job_id}/document",
        "/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/document",
    ],
)
def test_workspace_returns_owner_scoped_original_pdf(document_path: str) -> None:
    service, batch_id, job_id = _completed_batch_service()
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_document_store] = lambda: FakeDocumentStore()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        response = client.get(
            document_path.format(batch_id=batch_id, job_id=job_id),
            params={"anonymous_fingerprint": "browser-fingerprint"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert "x-frame-options" not in response.headers
    assert "http://localhost:3000" in response.headers["content-security-policy"]
    assert response.content.startswith(b"%PDF-1.4")


def test_workspace_persists_reviewed_state_in_batch_result() -> None:
    service, batch_id, job_id = _completed_batch_service()
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        reviewed = client.post(
            f"/api/conversion-batches/{batch_id}/jobs/{job_id}/review",
            json={"anonymous_fingerprint": "browser-fingerprint"},
        )
        reloaded = client.get(
            f"/api/conversion-batches/{batch_id}",
            params={"anonymous_fingerprint": "browser-fingerprint"},
        )
    finally:
        app.dependency_overrides.clear()

    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_at"]
    assert reloaded.status_code == 200
    assert reloaded.json()["jobs"][0]["reviewed_at"] == reviewed.json()["reviewed_at"]


def test_workspace_exports_available_reports_without_failed_jobs(tmp_path: Path) -> None:
    service, batch_id, _job_id = _completed_batch_service()
    (tmp_path / "an_workspace.ofx").write_text("OFXHEADER:100\n", encoding="utf-8")
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_async_conversion_report_service] = lambda: FakeBatchReportService(tmp_path)
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        response = client.get(
            f"/api/conversion-batches/{batch_id}/export",
            params={"anonymous_fingerprint": "browser-fingerprint"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-conversion-count"] == "1"
    assert response.content.startswith(b"PK")


def test_workspace_retries_only_failed_job_without_recreating_batch() -> None:
    publisher = FakePublisher(calls=[])
    service = ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=publisher,
    )
    identity = FakeAccessControlService().resolve_identity(
        anonymous_fingerprint="browser-fingerprint",
        user_token=None,
    )
    prepared = service.create(
        identity=identity,
        files=[
            ConversionBatchFile(
                filename="falha.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                sha256_hex="2" * 64,
            )
        ],
        idempotency_key="workspace-retry",
    )
    job_id = prepared.snapshot.jobs[0].job.job_id
    service.repository.mark_failed(job_id, code="temporary_failure")
    batch_id = prepared.snapshot.batch.batch_id
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        response = client.post(
            f"/api/conversion-batches/{batch_id}/jobs/{job_id}/retry",
            json={"anonymous_fingerprint": "browser-fingerprint"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["batch_id"] == batch_id
    assert response.json()["jobs"][0]["status"] == "queued"
    assert publisher.calls == [job_id]
