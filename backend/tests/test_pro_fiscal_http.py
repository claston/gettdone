"""PRO fiscal requests cross a real HTTP listener and use PostgreSQL."""

import hashlib

import pytest
from test_canonical_movements_postgres import DSN
from test_pro_fiscal_documents import nfe
from test_pro_fiscal_service import Objects, Queue, insert_note, setup

from app.application.pro_fiscal_documents import parse_fiscal_xml
from app.dependencies import get_access_control_service
from app.main import app
from app.routers.pro_fiscal import fiscal_service
from tests.http_runtime import LoopbackClient
from tests.test_canonical_pro import database as paid_database
from tests.test_convert_edits_http_real_api import _run_http_server

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable PostgreSQL")


def test_paid_http_import_link_classify_export_and_access_guards(database, monkeypatch):
    service, scope, movement = setup(database)
    service.objects, service.queue = Objects(), Queue()
    for name in ("PRO_FISCAL_ENABLED", "PRO_TAX_ENABLED", "PRO_REVENUE_ENABLED", "PRO_ORGANIZATION_ENABLED"):
        monkeypatch.setenv(name, "true")

    def configure(_):
        app.dependency_overrides[fiscal_service] = lambda: service
        app.dependency_overrides[get_access_control_service] = lambda: service.repo.test_access

    headers = {"Authorization": "Bearer " + service.repo.test_user.token, "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
    base = f"/api/pro/financial-center/clients/{scope.client_id}"
    raw = nfe(value="10.00")
    digest = hashlib.sha256(raw).hexdigest()
    with _run_http_server(configure) as url, LoopbackClient(base_url=url, headers=headers) as client:
        created = client.post(base + "/fiscal/imports", json={"filename": "nota.xml", "size_bytes": len(raw), "digest": digest})
        assert created.status_code == 201, created.text
        imported = created.json()["id"]
        service.objects.data[digest] = raw
        assert client.post(base + f"/fiscal/imports/{imported}/submit").status_code == 202
        service.complete_import(imported, lambda raw, kind: parse_fiscal_xml(raw))
        snapshot = client.get(base + "/fiscal?month=2026-04")
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.headers["cache-control"] == "private, no-store"
        note = snapshot.json()["documents"][0]
        source = client.get(base + f"/fiscal/documents/{note['id']}/source")
        assert source.content == raw and "attachment" in source.headers["content-disposition"]
        link = {
            "document_id": note["id"],
            "movement_id": movement,
            "amount": "10.00",
            "document_version": 1,
            "movement_version": 1,
            "operation_id": "http-link",
        }
        response = client.post(base + "/fiscal/links", json=link)
        assert response.status_code == 200, response.text
        assert client.post(base + "/fiscal/links", json=link).json() == response.json()
        classification = {
            "category": "operating_receipt",
            "expected_version": 0,
            "movement_version": 1,
            "operation_id": "http-classify",
            "details": {},
        }
        assert client.post(base + f"/classifications/{movement}", json=classification).status_code == 200
        export = client.get(base + "/fiscal/export?month=2026-04")
        assert export.status_code == 200 and "movimento" in export.text and "vínculo" in export.text
        assert (
            client.post(base + "/fiscal/links", json={**link, "operation_id": "bad"}, headers={"Origin": "https://other.test"}).status_code
            == 403
        )
        assert client.get(base + "/fiscal?month=invalid").status_code == 400
        assert client.get(base.replace(scope.client_id, "another-client") + "/fiscal?month=2026-04").status_code == 404
        free = service.repo.test_access.register_user(name="Free", email="free-fiscal@example.test", password="strong-password")
        assert client.get(base + "/fiscal?month=2026-04", headers={"Authorization": "Bearer " + free.token}).status_code == 403
        monkeypatch.setenv("PRO_FISCAL_ENABLED", "false")
        assert client.post(base + "/fiscal/links", json=link).status_code == 503
        disabled = client.get(base + "/fiscal?month=2026-04").json()
        assert disabled["documents"] == [] and disabled["links"] == []
        assert disabled["summary"]["documented_sales"] is None


def test_rollback_keeps_additive_data_and_reupgrade_is_idempotent(database, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    service, scope, _ = setup(database)
    identifier = insert_note(service, scope)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DATABASE_SCHEMA", service.repo.schema)
    command.downgrade(config, "20261115_01")
    with service.repo._connect() as conn:
        assert conn.execute(f"SELECT id FROM {service.repo.p}pro_fiscal_documents WHERE id=%s", (identifier,)).fetchone()
    command.upgrade(config, "head")
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-11")["documents"][0]["id"] == identifier
