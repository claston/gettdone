"""Real HTTP, SQL isolation, stale evidence and additive rollback for the pilot."""
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_canonical_movements_postgres import DSN
from test_classification_shadow import invoice
from test_pro_fiscal_service import insert_note, setup

from alembic import command
from alembic.config import Config
from app.application.classification_shadow.service import ShadowService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.dependencies import get_access_control_service
from app.main import app
from app.routers.classification_shadow import shadow_service
from tests.http_runtime import LoopbackClient
from tests.test_canonical_pro import database as paid_database
from tests.test_convert_edits_http_real_api import _run_http_server

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable PostgreSQL")


def test_shadow_is_idempotent_isolated_and_invalidates_on_edit(database):
    fiscal, scope, movement = setup(database)
    service = ShadowService(fiscal)
    args = dict(source_kind="movement", source_id=movement, source_version=1)
    before = fiscal.workspace(scope.actor_id, scope.client_id, month="2026-04")
    first = service.start(scope.actor_id, scope.client_id, **args)
    assert first["state"] == "ready" and not first["stale"]
    assert service.start(scope.actor_id, scope.client_id, **args)["id"] == first["id"]
    service.evaluate(scope.actor_id, scope.client_id, first["id"], item_id=movement, verdict="unknown")
    assert fiscal.workspace(scope.actor_id, scope.client_id, month="2026-04") == before
    with pytest.raises(AnalysisNotFoundError):
        service.read(scope.actor_id, "another-client", first["id"])
    fiscal.repo.apply(scope, [{"movement_id": movement, "date": "2026-04-01", "description": "Changed", "credit": 11}],
                     expected_version=1, operation_id="shadow-source-edit", channel="pro")
    assert service.read(scope.actor_id, scope.client_id, first["id"])["stale"]
    with pytest.raises(AnalysisEditConflictError):
        service.evaluate(scope.actor_id, scope.client_id, first["id"], item_id=movement, verdict="agree")


def test_real_http_allowlist_csrf_feedback_and_paid_access(database, monkeypatch):
    fiscal, scope, movement = setup(database)
    service = ShadowService(fiscal)
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    monkeypatch.setenv("CLASSIFICATION_SHADOW_MODE", "shadow")
    monkeypatch.setenv("CLASSIFICATION_SHADOW_USER_IDS", scope.actor_id)
    def configure(_):
        app.dependency_overrides[shadow_service] = lambda: service
        app.dependency_overrides[get_access_control_service] = lambda: fiscal.repo.test_access
    headers = {"Authorization": "Bearer " + fiscal.repo.test_user.token, "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
    base = f"/api/pro/financial-center/clients/{scope.client_id}/classification-shadow"
    args = dict(source_kind="movement", source_id=movement, source_version=1)
    with _run_http_server(configure) as url, LoopbackClient(base_url=url, headers=headers) as client:
        response = client.post(base + "/runs", json=args)
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        assert client.get(base + "/runs/" + identifier).headers["cache-control"] == "private, no-store"
        feedback = client.post(base + f"/runs/{identifier}/feedback", json={"item_id": movement, "verdict": "agree"})
        assert feedback.status_code == 200 and feedback.json()["metrics"]["agree"] == 1
        assert client.post(base + "/runs", json=args, headers={"Origin": "https://other.test"}).status_code == 403
        monkeypatch.setenv("CLASSIFICATION_SHADOW_USER_IDS", "other-user")
        assert client.get(base + "/runs/" + identifier).status_code == 404
        monkeypatch.setenv("CLASSIFICATION_SHADOW_USER_IDS", scope.actor_id)
        monkeypatch.setenv("CLASSIFICATION_SHADOW_MODE", "off")
        assert client.post(base + "/runs", json=args).status_code == 404


def test_populated_upgrade_rollback_and_expiry_cleanup(database, monkeypatch):
    fiscal, scope, movement = setup(database)
    service = ShadowService(fiscal)
    result = service.start(scope.actor_id, scope.client_id, source_kind="movement", source_id=movement, source_version=1)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DATABASE_SCHEMA", fiscal.repo.schema)
    command.downgrade(config, "20261116_01")
    command.upgrade(config, "head")
    assert service.read(scope.actor_id, scope.client_id, result["id"])["state"] == "ready"
    with fiscal.repo._connect() as conn:
        conn.execute(f"UPDATE {fiscal.repo.p}classification_shadow_runs SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(AnalysisNotFoundError):
        service.read(scope.actor_id, scope.client_id, result["id"])
    fiscal.objects = Mock()
    fiscal.cleanup()
    with fiscal.repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {fiscal.repo.p}classification_shadow_runs").fetchone()["n"] == 0


def test_fiscal_outbox_rejects_partial_or_wrong_results_and_keeps_all_items(database):
    fiscal, scope, _ = setup(database)
    identifier = insert_note(fiscal, scope)
    fiscal.source = Mock(return_value=(invoice(), {"file_type": "xml"}))
    transport = Mock()
    transport.result.return_value = None
    transport.publish.side_effect = RuntimeError("temporary queue failure")
    service = ShadowService(fiscal, transport)
    run = service.start(scope.actor_id, scope.client_id, source_kind="invoice", source_id=identifier, source_version=1)
    assert run["state"] == "queued"
    request = transport.publish.call_args.args[0]
    transport.result.return_value = {**request, "items": []}
    with pytest.raises(ValueError, match="corresponde"):
        service.read(scope.actor_id, scope.client_id, run["id"])
    transport.result.return_value = {**request, "input_hash": "wrong"}
    with pytest.raises(ValueError, match="corresponde"):
        service.read(scope.actor_id, scope.client_id, run["id"])
    transport.result.return_value = {**request, "items": [{"id": "1", "status": "review_required", "candidates": []}]}
    assert service.read(scope.actor_id, scope.client_id, run["id"])["state"] == "ready"
    assert transport.publish.call_count == 1
