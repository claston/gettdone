"""Conditional workspace reads against real PostgreSQL and HTTP."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_canonical_movements_postgres import DSN, enroll
from test_canonical_pro import database as canonical_database
from test_canonical_pro import enable

from app.application.canonical_pro import CanonicalProService
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.received_statements import ReceivedStatements
from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config
from app.main import app
from app.routers import canonical_pro
from app.routers.received_statements import receipt_service
from tests.http_runtime import LoopbackClient
from tests.test_convert_edits_http_real_api import _run_http_server

pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


@pytest.fixture
def workspace_api(database, monkeypatch):
    repository, scope, _, _, batches, _ = database
    receipts = ReceivedStatements(repository.test_access.db, batches)
    receipts.receive(batches.get(scope.job_id))
    enroll(database)
    enable(database)
    service = CanonicalProService(repository, None)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: service)
    batch_id = batches.get(scope.job_id).job.batch_id

    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repository.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig(
            architecture_mode="async_aws", upload_mode="api", execution_mode="local", batch_max_files=12)
        app.dependency_overrides[receipt_service] = lambda: receipts

    with _run_http_server(configure) as base, LoopbackClient(base_url=base, timeout=30,
            headers={"Authorization": f"Bearer {repository.test_user.token}"}) as client:
        yield client, repository, scope, batches, batch_id


@pytest.mark.parametrize("view", ["batch", "client"])
def test_unchanged_refresh_skips_payload_and_movements_then_observes_edit(workspace_api, view):
    client, repository, scope, batches, batch_id = workspace_api
    path = (f"/api/pro/conversion-batches/{batch_id}" if view == "batch"
            else f"/api/pro/receipts/clients/{scope.client_id}/workspace")
    with patch.object(repository, "_movements", wraps=repository._movements) as movements:
        first = client.get(path)
        assert first.status_code == 200, first.text
        assert movements.call_count == 1
    tag = first.headers["etag"]
    with patch.object(repository, "_movements", side_effect=AssertionError("No movements on 304")), \
            patch.object(batches, "get_for_owner", side_effect=AssertionError("No full batch on 304")), \
            patch.object(batches, "get", side_effect=AssertionError("No full job on 304")):
        unchanged = client.get(path, headers={"If-None-Match": tag})
        assert unchanged.status_code == 304, unchanged.text
        assert not unchanged.content
    repository.apply(scope, [{"row_id": "stable", "description": "Remote edit", "date": "2026-04-01", "credit": 10}],
                     expected_version=1, operation_id="refresh-test", channel="pro")
    changed = client.get(path, headers={"If-None-Match": tag})
    assert changed.status_code == 200, changed.text
    assert changed.headers["etag"] != tag
    assert changed.json()["jobs"][0]["result"]["analysis"]["version"] == 2


@pytest.mark.parametrize("mutation,expected", [
    ("UPDATE {p}canonical_analyses SET read_enabled=FALSE", 409),
    ("UPDATE {p}conversion_batches SET expires_at=clock_timestamp()-interval '1 second'", 404),
])
def test_validator_never_hides_revocation_or_expiry(workspace_api, mutation, expected):
    client, repository, _, _, batch_id = workspace_api
    path = f"/api/pro/conversion-batches/{batch_id}"
    initial = client.get(path)
    assert initial.status_code == 200, initial.text
    with repository._connect() as conn:
        conn.execute(mutation.format(p=repository.p))
    assert client.get(path, headers={"If-None-Match": initial.headers["etag"]}).status_code == expected


def test_free_route_remains_unconditional(workspace_api):
    client, _, _, _, batch_id = workspace_api
    response = client.get(f"/api/conversion-batches/{batch_id}", headers={"If-None-Match": "*"})
    assert response.status_code == 200
    assert "etag" not in response.headers


def test_client_validator_covers_later_pages_membership_and_name(workspace_api):
    client, repository, scope, batches, _ = workspace_api
    original = batches.get(scope.job_id).job
    jobs = []
    for start in range(0, 51, 12):
        count = min(12, 51-start)
        batch = ConversionBatch.create(identity_type="user", identity_id=scope.actor_id,
                                       files_count=count, idempotency_key=f"pages-{start}")
        page = [ConversionJob.create(batch_id=batch.batch_id, identity=original.identity, document=original.document,
                                     idempotency_key=f"page-{start+i}") for i in range(count)]
        batches.create(batch=batch, jobs=page)
        jobs.extend(page)
    receipts = ReceivedStatements(repository.test_access.db, batches)
    for job in jobs:
        receipts.save_context(scope.actor_id, job_id=job.job_id, client_id=scope.client_id,
                              account_id=None, period_ids=[], expected_version=0)
        receipts.receive(batches.get(job.job_id))
    path = f"/api/pro/receipts/clients/{scope.client_id}/workspace"
    first = client.get(path)
    assert first.status_code == 200, first.text
    tag = first.headers["etag"]
    assert len(first.json()["jobs"]) == 50 and first.json()["next_cursor"]
    last_job = max([job.job_id for job in jobs] + [scope.job_id])
    with repository._connect() as conn:
        conn.execute(f'UPDATE {repository.p}conversion_jobs SET updated_at=clock_timestamp() WHERE job_id=%s', (last_job,))
    changed = client.get(path, headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag
    tag = changed.headers["etag"]
    with repository._connect() as conn:
        conn.execute(f'UPDATE {repository.p}pro_clients SET name=%s WHERE id=%s', ("Renamed", scope.client_id))
    renamed = client.get(path, headers={"If-None-Match": tag})
    assert renamed.json()["client_name"] == "Renamed"
    with repository._connect() as conn:
        conn.execute(f'DELETE FROM {repository.p}pro_received_statements WHERE job_id=%s', (last_job,))
    removed = client.get(path, headers={"If-None-Match": renamed.headers["etag"]})
    assert removed.status_code == 200 and removed.headers["etag"] != renamed.headers["etag"]


def test_elapsed_ttl_invalidates_without_an_update(workspace_api):
    client, repository, scope, _, _ = workspace_api
    path = f"/api/pro/receipts/clients/{scope.client_id}/workspace"
    with repository._connect() as conn:
        conn.execute(f"UPDATE {repository.p}pro_received_statements SET expires_at=%s",
                     ((datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),))
    first = client.get(path)
    assert first.status_code == 200 and len(first.json()["jobs"]) == 1
    with repository._connect() as conn:
        conn.execute("SELECT pg_sleep(2)")
    expired = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert expired.status_code == 200 and expired.json()["jobs"] == []


@pytest.mark.parametrize("view", ["batch", "client"])
def test_validator_cannot_reuse_another_owners_workspace(workspace_api, view):
    client, repository, scope, _, batch_id = workspace_api
    path = (f"/api/pro/conversion-batches/{batch_id}" if view == "batch"
            else f"/api/pro/receipts/clients/{scope.client_id}/workspace")
    first = client.get(path, headers={"Origin": "http://localhost:3000"})
    assert first.status_code == 200
    assert "etag" in first.headers["access-control-expose-headers"].lower()
    user = repository.test_access.register_user(name="Other", email="other@example.test", password="strong-password")
    repository.test_access.activate_user_plan(user_id=user.user_id, plan_code="essencial")
    denied = client.get(path, headers={"If-None-Match": first.headers["etag"], "Authorization": f"Bearer {user.token}"})
    assert denied.status_code == 404, denied.text
