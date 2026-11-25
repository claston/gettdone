"""Documentary links never allocate cash or mark a statement reconciled."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb
from test_canonical_movements_postgres import DSN
from test_canonical_pro import database as paid_database
from test_financial_center import add_document, adopt

from app.application.document_conference import DocumentConferenceService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


def setup(database):
    repo, scope, *_ = database
    initial, _ = adopt(database)
    record, content = add_document(database, suffix="support", rows=[])
    content = deepcopy(content)
    content.update(document_type="bank_receipt", financial_document={
        "document_type": "bank_receipt", "observations": [{"observation_type": "payment",
        "dates": {"payment_date": "2026-04-01"}, "amounts": {"paid_amount": "10.00"},
        "attributes": {"recipient": "Empresa exemplo"}, "source_refs": [{"page": 1, "line": 2}]}]})
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET result_payload=%s WHERE job_id=%s",
                     (Jsonb({"analysis": content}), record.job.job_id))
    service = DocumentConferenceService(repo)
    return service, scope, initial["movement_ids"]["stable"], record.job.job_id


def test_confirm_undo_and_repeat_preserve_bank_values(database):
    service, scope, movement, job = setup(database)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    request = dict(job_id=job, source=evidence["source"], movement_version=1, operation_id="document-link")
    linked = service.link_document(scope.actor_id, scope.client_id, movement, **request)
    assert service.link_document(scope.actor_id, scope.client_id, movement, **request) == linked
    data = service.movement_links(scope.actor_id, scope.client_id, movement)
    assert data["links"][0]["state"] == "confirmed"
    assert data["links"][0]["document"]["job_id"] == job
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-04")["movements"][0]["amount"] == "10.00"
    service.unlink_document(scope.actor_id, scope.client_id, movement, linked["id"], expected_version=1, operation_id="undo")
    assert service.movement_links(scope.actor_id, scope.client_id, movement)["links"] == []


def test_source_and_movement_edits_require_new_confirmation(database):
    service, scope, movement, job = setup(database)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                          movement_version=1, operation_id="link")
    service.repo.apply(scope, [{"movement_id": movement, "date": "2026-04-01", "description": "Corrigido", "credit": 11}],
                       expected_version=1, operation_id="edit", channel="pro")
    assert service.movement_links(scope.actor_id, scope.client_id, movement)["links"][0]["state"] == "review_required"
    with pytest.raises(AnalysisEditConflictError):
        service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                              movement_version=1, operation_id="stale")
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}conversion_jobs SET updated_at=clock_timestamp() WHERE job_id=%s", (job,))
    with pytest.raises(AnalysisEditConflictError):
        service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                              movement_version=2, operation_id="stale-doc")


def test_owner_client_and_expiry_guards(database):
    service, scope, movement, job = setup(database)
    with pytest.raises(AnalysisNotFoundError):
        service.document(scope.actor_id, "different-client", job)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                          movement_version=1, operation_id="link")
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}conversion_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (job,))
    with pytest.raises(AnalysisNotFoundError):
        service.document(scope.actor_id, scope.client_id, job)
    data = service.movement_links(scope.actor_id, scope.client_id, movement)
    assert data["links"] == []
    assert data["unavailable_links"] == 1


def test_rejects_statement_as_support_and_never_suggests_from_equal_amount(database):
    service, scope, movement, _ = setup(database)
    with pytest.raises(ValueError, match="extrato"):
        service.document(scope.actor_id, scope.client_id, scope.job_id)
    assert service.movement_links(scope.actor_id, scope.client_id, movement)["links"] == []


def test_independent_connections_confirm_once_and_reject_other_owner(database):
    service, scope, movement, job = setup(database)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    def confirm(operation):
        return DocumentConferenceService(service.repo).link_document(scope.actor_id, scope.client_id, movement,
            job_id=job, source=evidence["source"], movement_version=1, operation_id=operation)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(confirm, ("first", "second")))
    assert results[0] == results[1]
    other = service.repo.test_access.register_user(name="Other", email="other@example.test", password="strong-password")
    service.repo.test_access.activate_user_plan(user_id=other.user_id, plan_code="essencial")
    with pytest.raises(AnalysisNotFoundError):
        service.movement_links(other.user_id, scope.client_id, movement)
    with pytest.raises(AnalysisNotFoundError):
        service.document(other.user_id, scope.client_id, job)
    with pytest.raises(AnalysisNotFoundError):
        service.link_document(other.user_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                              movement_version=1, operation_id="intruder")
    with pytest.raises(AnalysisEditConflictError):
        service.unlink_document(scope.actor_id, scope.client_id, movement, results[0]["id"],
                                expected_version=99, operation_id="stale-undo")


def test_uses_current_document_authority_when_batch_projection_lags(database):
    service, scope, movement, job = setup(database)
    record = database[4].get(job)
    service.current_document = lambda _owner, _job: record
    evidence = service.document(scope.actor_id, scope.client_id, job)
    service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                          movement_version=1, operation_id="link")
    payload = deepcopy(record.result.payload)
    payload["analysis"]["financial_document"]["notice"] = "Valor precisa de conferência."
    record = replace(record, result=replace(record.result, payload=payload))
    assert service.movement_links(scope.actor_id, scope.client_id, movement)["links"][0]["state"] == "review_required"
    with pytest.raises(AnalysisEditConflictError):
        service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                              movement_version=1, operation_id="stale-authority")


def test_migration_rollback_reupgrade_and_cleanup_preserve_existing_jobs(database, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.application.office_documents import OfficeReviews

    service, scope, movement, job = setup(database)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence["source"],
                          movement_version=1, operation_id="link")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DATABASE_SCHEMA", service.repo.schema)
    command.downgrade(config, "20261123_01")
    assert database[4].get(scope.job_id).result
    assert service.movement_links(scope.actor_id, scope.client_id, movement)["links"][0]["state"] == "confirmed"
    command.upgrade(config, "head")
    assert len(service.movement_links(scope.actor_id, scope.client_id, movement)["links"]) == 1
    OfficeReviews(service.repo.test_access.db).purge(now=datetime.now(timezone.utc) + timedelta(days=2))
    with service.repo._connect() as conn:
        for table in ("pro_document_movement_links", "pro_document_link_events"):
            assert conn.execute(f"SELECT count(*) AS n FROM {service.repo.p}{table}").fetchone()["n"] == 0
    assert database[4].get(scope.job_id).result


def test_real_http_confirmation_undo_conflict_and_owner_checks(database, tmp_path, monkeypatch):
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_conversion_batch_service,
        get_report_service,
    )
    from app.main import app
    from app.routers import canonical_pro, financial_center
    from tests.http_runtime import LoopbackClient
    from tests.test_convert_edits_http_real_api import _run_http_server

    service, scope, movement, job = setup(database)
    canonical = CanonicalProService(service.repo, None)
    monkeypatch.setenv("PRO_FINANCIAL_CENTER_ENABLED", "true")
    monkeypatch.setattr(financial_center, "get_canonical_pro_service", lambda: canonical)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: canonical)
    report = ReportService(TempAnalysisStorage(tmp_path / "legacy"))
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: service.repo.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=database[4])
        app.dependency_overrides[get_report_service] = lambda: report
        app.dependency_overrides[get_async_conversion_report_service] = lambda: report
    with _run_http_server(configure) as api, LoopbackClient(base_url=api, headers={
        "Authorization": f"Bearer {service.repo.test_user.token}", "Origin": "http://localhost:3000",
        "X-OFX-Pro-Client": "web",
    }) as client:
        root = f"/api/pro/documents/conference/{scope.client_id}"
        evidence = client.get(f"{root}/documents/{job}")
        assert evidence.status_code == 200, evidence.text
        request = dict(job_id=job, source=evidence.json()["source"], movement_version=1, operation_id="http")
        saved = client.post(f"{root}/movements/{movement}/links", json=request)
        assert saved.status_code == 200, saved.text
        assert client.post(f"{root}/movements/{movement}/links", json=request).json() == saved.json()
        assert client.get(f"{root}/movements/{movement}").json()["links"][0]["state"] == "confirmed"
        assert client.post(f"{root}/movements/{movement}/links", json={**request, "source": "0" * 64}).status_code == 409
        undo = client.post(f"{root}/movements/{movement}/links/{saved.json()['id']}/undo",
                           json={"expected_version": 1, "operation_id": "undo-http"})
        assert undo.status_code == 200, undo.text
        assert client.get(f"{root}/movements/{movement}").json()["links"] == []
        assert client.get(f"{root}/documents/nonexistent").status_code == 404
        assert client.post(f"{root}/movements/{movement}/links", json={**request, "job_id": scope.job_id},
                           timeout=5).status_code == 400
        assert client.post(f"{root}/movements/{movement}/links", json={**request, "unexpected": True}).status_code == 400
        assert client.post(f"{root}/movements/{movement}/links", json=request,
                           headers={"Origin": "https://untrusted.example", "X-OFX-Pro-Client": ""}).status_code == 403
