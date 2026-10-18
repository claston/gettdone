from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.conversion.memory_batches import InMemoryConversionBatchRepository
from app.application import AccessControlService
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config
from app.main import app
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME


def _seed_batch(repository, identity, *, created_at, number):
    batch = ConversionBatch.create(identity_type=identity.identity_type, identity_id=identity.identity_id,
                                   files_count=1, idempotency_key=f"list-{number}", created_at=created_at)
    job = ConversionJob.create(batch_id=batch.batch_id, identity=identity, idempotency_key=f"list-job-{number}",
        document=ConversionDocumentReference(storage_key=f"doc_list_{number}", filename="private-name.pdf",
                                             file_type="pdf", size_bytes=100, sha256_hex="0" * 64))
    repository.create(batch=batch, jobs=[job])
    repository.mark_uploaded(job.job_id)
    repository.mark_queued(job.job_id)
    repository.mark_running(job.job_id)
    repository.mark_completed(job.job_id, result=ConversionJobResultReference(
        analysis_id=f"an_list_{number}", payload={"analysis": {"preview_transactions": [{"description": "PRIVATE FINANCIAL DATA"}]}},
    ))
    if created_at < datetime.now(timezone.utc) - timedelta(days=1):
        repository._jobs[job.job_id] = replace(repository._jobs[job.job_id], expires_at=created_at + timedelta(hours=24))
    return batch, job


def test_batch_listing_is_authenticated_paginated_metadata_only_and_owner_scoped(tmp_path):
    access = AccessControlService(state_file=tmp_path / "auth.json", token_secret="listing-test")
    user = access.register_user(name="Owner", email="owner@test.example", password="strong-password")
    other = access.register_user(name="Other", email="other@test.example", password="strong-password")
    owner = access.resolve_identity(user_token=user.token, anonymous_fingerprint=None)
    outsider = access.resolve_identity(user_token=other.token, anonymous_fingerprint=None)
    repository = InMemoryConversionBatchRepository()
    timestamp = datetime.now(timezone.utc)
    batches = [_seed_batch(repository, owner, created_at=timestamp, number=number)[0] for number in range(4)]
    expired, _ = _seed_batch(repository, owner, created_at=timestamp - timedelta(days=2), number=4)
    _seed_batch(repository, outsider, created_at=timestamp, number=5)
    app.dependency_overrides[get_access_control_service] = lambda: access
    app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=repository)
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({
        "CONVERSION_ARCHITECTURE_MODE": "async_aws",
    })
    try:
        client = TestClient(app)
        url = "/api/pro/conversion-batches"
        assert client.get(url).status_code == 401
        headers = {"Authorization": f"Bearer {user.token}"}
        items, cursor = [], None
        for _ in range(3):
            response = client.get(url, headers=headers, params={"limit": 2, **({"cursor": cursor} if cursor else {})})
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            payload = response.json()
            items.extend(payload["items"])
            cursor = payload["next_cursor"]
        assert cursor is None
        assert [item["batch_id"] for item in items] == sorted([batch.batch_id for batch in batches], reverse=True) + [expired.batch_id]
        assert len({item["batch_id"] for item in items}) == 5
        assert items[-1]["is_expired"]
        assert items[-1]["first_job_id"] is None
        assert "PRIVATE FINANCIAL DATA" not in str(items)
        assert "private-name.pdf" not in str(items)
        assert all(item["completed_count"] == 1 for item in items)
        assert client.get(url, headers=headers, params={"cursor": "invalid"}).status_code == 400
        assert client.get(url, headers=headers, params={"limit": 1000}).status_code == 400
        outside = client.get(url, headers={"Authorization": f"Bearer {other.token}"}).json()
        assert len(outside["items"]) == 1
        assert outside["items"][0]["batch_id"] not in {item["batch_id"] for item in items}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.real_http
def test_http_batch_listing_uses_session_cookie_without_origin_and_requires_login():
    from test_convert_edits_http_real_api import _run_http_server

    state = {}

    def configure(access):
        user = access.register_user(name="Owner", email="history@test.example", password="strong-password")
        owner = access.resolve_identity(user_token=user.token, anonymous_fingerprint=None)
        repository = InMemoryConversionBatchRepository()
        for number in range(3):
            _seed_batch(repository, owner, created_at=datetime.now(timezone.utc), number=number)
        state["access"] = access.create_user_session(user_id=user.user_id).access_token
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=repository)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({
            "CONVERSION_ARCHITECTURE_MODE": "async_aws",
        })

    with _run_http_server(configure) as base_url:
        url = f"{base_url}/api/pro/conversion-batches"
        assert httpx.get(url).status_code == 401
        with httpx.Client(cookies={SESSION_ACCESS_COOKIE_NAME: state["access"]}) as client:
            first = client.get(url, params={"limit": 2})
            assert first.status_code == 200
            assert len(first.json()["items"]) == 2
            second = client.get(url, params={"limit": 2, "cursor": first.json()["next_cursor"]})
            assert second.status_code == 200
            assert len(second.json()["items"]) == 1
            assert second.json()["next_cursor"] is None
            assert "no-store" in second.headers["cache-control"]
