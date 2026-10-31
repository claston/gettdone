"""Individual paid conversion intent and no-cost page estimate through HTTP."""

import pytest
from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.dependencies import (
    get_access_control_service,
    get_conversion_batch_document_store,
    get_conversion_batch_service,
    get_conversion_runtime_config,
)
from app.main import app
from app.routers import conversion_pro
from tests.test_conversion_batches_api import FakeDirectUploadService, FakePublisher, _request_payload


@pytest.fixture
def context(tmp_path, monkeypatch):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="test")
    user = access.register_user(name="Test", email="pro@example.test", password="strong-password")
    access.activate_user_plan(user_id=user.user_id, plan_code="profissional")
    identity = access.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    service = ConversionBatchService(InMemoryConversionBatchRepository(), FakeDirectUploadService(), FakePublisher(calls=[]))
    runtime = ConversionRuntimeConfig.from_mapping({"CONVERSION_ARCHITECTURE_MODE": "async_aws"})

    class Store:
        def load(self, ref):
            return type("Document", (), {"raw_bytes": b"private test PDF"})()

    monkeypatch.setenv("CONVERSION_PRO_ENABLED", "true")
    monkeypatch.setattr(conversion_pro, "inspect_pro_pdf", lambda raw, max_pages: 4)
    app.dependency_overrides[get_access_control_service] = lambda: access
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_runtime_config] = lambda: runtime
    app.dependency_overrides[get_conversion_batch_document_store] = lambda: Store()
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {user.token}",
        "Idempotency-Key": "pro-one",
        "X-OFX-Pro-Client": "web",
        "Origin": "http://localhost:3000",
    }
    yield client, service, access, identity, headers
    app.dependency_overrides.clear()
    access.close()


def create(client, headers, count=1, mode="pro"):
    body = _request_payload(count)
    body.pop("anonymous_fingerprint")
    body["ocr_mode"] = mode
    return client.post("/api/pro/conversion-batches", json=body, headers=headers)


def test_paid_mode_is_persisted_and_estimate_does_not_consume_quota(context):
    client, service, access, identity, headers = context
    options = client.get("/api/pro/conversion-options", headers=headers)
    assert options.status_code == 200 and options.json()["eligible"]
    response = create(client, headers)
    assert response.status_code == 201, response.text
    batch = response.json()["batch_id"]
    snapshot = service.repository.get_for_owner(batch, identity_type="user", identity_id=identity.identity_id)
    assert snapshot.jobs[0].job.ocr_mode == "pro"
    estimate = client.get(f"/api/pro/conversion-batches/{batch}/pro-estimate", headers=headers)
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["pages"] == 4 and estimate.json()["remaining_after"] == 16
    with access._connect() as conn:
        assert access._fetchone(conn, "SELECT COUNT(*) AS n FROM conversion_pro_usage")["n"] == 0


def test_disabled_and_free_modes_are_rejected_before_upload_preparation(context, monkeypatch):
    client, service, access, identity, headers = context
    monkeypatch.setenv("CONVERSION_PRO_ENABLED", "false")
    assert create(client, headers).status_code == 403
    monkeypatch.setenv("CONVERSION_PRO_ENABLED", "true")
    access.activate_user_plan(user_id=identity.identity_id, plan_code="essencial")
    with access._lock, access._connect() as conn:
        access._execute(conn, "UPDATE user_plan_subscriptions SET status='ended' WHERE user_id=?", (identity.identity_id,))
        conn.commit()
    access._active_plan_cache.clear()
    assert create(client, headers).status_code == 403


def test_pro_accepts_only_one_pdf_and_cannot_change_mode_on_idempotent_reuse(context):
    client, service, access, identity, headers = context
    assert create(client, headers, count=2).status_code == 400
    assert create(client, headers, mode="standard").status_code == 201
    changed = create(client, headers, mode="pro")
    assert changed.status_code == 400


def test_estimate_enforces_owner(context):
    client, service, access, identity, headers = context
    response = create(client, headers)
    batch = response.json()["batch_id"]
    other = access.register_user(name="Other", email="other@example.test", password="strong-password")
    access.activate_user_plan(user_id=other.user_id, plan_code="profissional")
    result = client.get(f"/api/pro/conversion-batches/{batch}/pro-estimate", headers={"Authorization": f"Bearer {other.token}"})
    assert result.status_code == 404
