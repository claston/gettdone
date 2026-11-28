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
        conn.execute(f"""UPDATE {service.repo.p}conversion_jobs SET updated_at=clock_timestamp(),
            result_payload=jsonb_set(result_payload,'{{analysis,updated_at}}',to_jsonb(clock_timestamp()::text)) WHERE job_id=%s""", (job,))
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
    import json

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.adapters.conversion.document_retention import analysis_retention_resolver
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_conversion_batch_service,
        get_report_service,
    )
    from app.main import app
    from app.routers import canonical_pro, document_preparation, financial_center
    from tests.http_runtime import LoopbackClient
    from tests.test_convert_edits_http_real_api import _run_http_server

    service, scope, movement, job = setup(database)
    canonical = CanonicalProService(service.repo, LocalCanonicalObjects(tmp_path / 'canonical'))
    monkeypatch.setenv("PRO_FINANCIAL_CENTER_ENABLED", "true")
    monkeypatch.setattr(financial_center, "get_canonical_pro_service", lambda: canonical)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: canonical)
    monkeypatch.setattr(document_preparation, "get_canonical_pro_service", lambda: canonical)
    storage = TempAnalysisStorage(tmp_path / "legacy", retention_resolver=analysis_retention_resolver(DSN, service.repo.schema))
    metadata = storage.root_dir / scope.analysis_id / 'analysis.json'
    metadata.parent.mkdir()
    metadata.write_text(json.dumps(database[2]), encoding='utf-8')
    storage.fence_canonical_analysis(scope.analysis_id)
    report = ReportService(storage)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: service.repo.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=database[4])
        app.dependency_overrides[get_report_service] = lambda: report
        app.dependency_overrides[get_async_conversion_report_service] = lambda: report
    with _run_http_server(configure) as api, LoopbackClient(base_url=api, headers={
        "Authorization": f"Bearer {service.repo.test_user.token}", "Origin": "http://localhost:3000",
        "X-OFX-Pro-Client": "web",
    }) as client:
        prepared = client.post(f'/api/pro/documents/{job}/preparation', json={})
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()['context']['client_id'] == scope.client_id
        assert prepared.json()['matching']['matches'][0]['automatic'] is False
        assert client.get('/api/pro/documents/retention').json()['retained_while_paid'] is True
        assert client.get('/api/pro/documents/absent/preparation').status_code == 404
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
        with service.repo._connect() as conn:
            conn.execute(f"UPDATE {service.repo.p}conversion_jobs SET product='pro',expires_at=expires_at")
            conn.execute(f"UPDATE {service.repo.p}user_plan_subscriptions SET status='ended',ended_at=%s WHERE user_id=%s",
                         (datetime.now(timezone.utc).isoformat(), scope.actor_id))
        policy = client.get('/api/pro/documents/retention')
        assert policy.status_code == 200 and policy.json()['read_only'] is True
        assert client.get(f'/api/pro/documents/{job}/preparation').status_code == 200
        assert client.post(f'/api/pro/documents/{job}/preparation', json={}).status_code == 403
        assert client.post('/api/pro/conversion-batches', headers={'Idempotency-Key': 'ended-plan'}, json={'files': [{
            'filename': 'novo.csv', 'content_type': 'text/csv', 'size_bytes': 1, 'sha256_hex': 'a'*64}]}).status_code == 403
        resolve_identity = service.repo.test_access.resolve_identity
        monkeypatch.setattr(service.repo.test_access, 'resolve_identity',
                            lambda **kwargs: replace(resolve_identity(**kwargs), batch_conversion_enabled=False))
        download = client.get(f'/convert-report/{scope.analysis_id}', params={'format': 'csv'})
        assert download.status_code == 200, download.text
        from app.application.errors import AnalysisAccessDeniedError
        assert canonical.read(scope)['preview_transactions']
        with pytest.raises(AnalysisAccessDeniedError):
            canonical.edit(scope, [], expected_version=1, operation_id='grace-edit')


def test_review_and_renewal_keep_documentary_links_but_financial_edits_do_not(database):
    service, scope, movement, job = setup(database)
    evidence = service.document(scope.actor_id, scope.client_id, job)
    service.link_document(scope.actor_id, scope.client_id, movement, job_id=job, source=evidence['source'],
                          movement_version=1, operation_id='initial-link')
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}conversion_jobs SET product='pro',expires_at=expires_at")
    database[4].mark_reviewed(job, reviewed_at=datetime.now(timezone.utc).isoformat())
    assert service.document(scope.actor_id, scope.client_id, job)['source'] == evidence['source']
    assert service.movement_links(scope.actor_id, scope.client_id, movement)['links'][0]['state'] == 'confirmed'
    from app.application.office_documents import OfficeReviews
    from app.routers.office_documents import describe
    info = describe(database[4].get(job), database[5].contexts(scope.actor_id, [job])[job],
                    OfficeReviews(service.repo.test_access.db), scope.actor_id)
    assert info['verification']['status'] == 'verified'
    # One confirmation in the converter is sufficient for document delivery.
    with service.repo._connect() as conn:
        assert conn.execute(f'SELECT count(*) AS n FROM {service.repo.p}pro_document_reviews').fetchone()['n'] == 0


def test_unique_identifier_links_automatically_and_respects_manual_undo(database):
    service, scope, movement, job = setup(database)
    identifier = 'E1234567820261127012345678901234'
    record = database[4].get(job)
    payload = deepcopy(record.result.payload)
    financial = payload['analysis']['financial_document']
    financial['metadata'] = {'currency': 'BRL'}
    financial['observations'][0]['attributes']['transaction_id'] = identifier
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}conversion_jobs SET result_payload=%s WHERE job_id=%s", (Jsonb(payload), job))
        conn.execute(f"UPDATE {service.repo.p}bank_movements SET attributes=%s WHERE id=%s",
                     (Jsonb({'document_number': identifier}), movement))
    found = service.suggestions(scope.actor_id, scope.client_id, job, automatic=True)
    assert len(found['matches']) == 1
    match = found['matches'][0]
    assert match['automatic'] and match['linked']
    service.unlink_document(scope.actor_id, scope.client_id, movement, match['link_id'],
                            expected_version=match['link_version'], operation_id='manual-undo')
    repeated = service.suggestions(scope.actor_id, scope.client_id, job, automatic=True)
    assert not repeated['matches'][0]['linked']


def test_preparation_associates_only_a_unique_strong_holder(database, monkeypatch):
    from app.routers.document_preparation import preparation
    service, scope, _, job = setup(database)
    repo, organizations = service.repo, database[5]
    payload = deepcopy(database[4].get(job).result.payload)
    payload['organization_source'] = {'holder_tax_id': '11222333000181'}
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}pro_clients SET tax_id='11222333000181' WHERE id=%s", (scope.client_id,))
        conn.execute(f"UPDATE {repo.p}pro_conversion_contexts SET client_id=NULL,account_id=NULL WHERE job_id=%s", (job,))
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET result_payload=%s WHERE job_id=%s", (Jsonb(payload), job))
    monkeypatch.setenv('PRO_FINANCIAL_CENTER_ENABLED', 'false')
    batches = SimpleNamespace(repository=database[4])
    result = preparation(job, scope.actor_id, organizations, batches, (), automatic=True)
    assert result['automatically_associated'] and result['context']['client_id'] == scope.client_id
    assert not preparation(job, scope.actor_id, organizations, batches, (), automatic=True)['automatically_associated']
