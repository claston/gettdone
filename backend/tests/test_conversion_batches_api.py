from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import dependencies
from app.application.access_control import IdentityContext, RegisteredUser
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.conversion.s3_direct_upload_service import PreparedS3Upload
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_service,
    get_conversion_runtime_config,
)
from app.main import app
from app.routers.auth_session import ANONYMOUS_IDENTITY_COOKIE_NAME


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
    def __init__(self, *, email: str) -> None:
        self.email = email

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
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    client = TestClient(app)
    try:
        created = client.post(
            "/api/conversion-batches",
            headers={"Idempotency-Key": "request-123", "X-Request-ID": "trace-123"},
            json=_request_payload(),
        )
        assert created.status_code == 201
        created_payload = created.json()
        assert created_payload["status"] == "uploading"
        assert len(created_payload["uploads"]) == 2
        assert created_payload["uploads"][0]["url"].startswith("https://")
        batch_id = created_payload["batch_id"]

        submitted = client.post(
            f"/api/conversion-batches/{batch_id}/submit",
            headers={"X-Request-ID": "trace-456"},
            json={"anonymous_fingerprint": "browser-fingerprint"},
        )
        assert submitted.status_code == 202
        assert submitted.json()["status"] == "queued"
        assert [item["status"] for item in submitted.json()["jobs"]] == ["queued", "queued"]

        status = client.get(
            f"/api/conversion-batches/{batch_id}",
            params={"anonymous_fingerprint": "browser-fingerprint"},
        )
        assert status.status_code == 200
        assert status.json()["batch_id"] == batch_id
    finally:
        app.dependency_overrides.clear()


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
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
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


def test_percentage_rollout_includes_anonymous_traffic_with_one_file_limit() -> None:
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({})
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
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
