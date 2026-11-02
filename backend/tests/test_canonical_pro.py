"""PRO integration against disposable PostgreSQL; legacy fixture remains unchanged."""
import pytest
from test_canonical_movements_postgres import DSN, enroll
from test_canonical_movements_postgres import database as canonical_database

from app.application.errors import AnalysisEditConflictError

pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


@pytest.fixture
def database(tmp_path):
    for value in canonical_database.__wrapped__(tmp_path):
        value[0].test_access.activate_user_plan(user_id=value[1].actor_id, plan_code="essencial")
        yield value


def test_movement_identity_and_version_cross_views(database):
    repository, scope, _, _, batches, _ = database
    initial = enroll(database)
    movement = initial["movement_ids"]["stable"]
    command = [{"movement_id": movement, "date": "2026-04-02", "description": "Cliente corrigido", "debit": 2.75}]
    saved = repository.apply(scope, command, expected_version=1, operation_id="client", channel="pro",
                             expected_movement_versions={movement: 1})
    assert saved["version"] == 2
    assert repository.apply(scope, command, expected_version=1, operation_id="client", channel="pro",
                            expected_movement_versions={movement: 1}) == saved
    reopened = batches.get(scope.job_id).result.payload["analysis"]
    assert reopened["preview_transactions"][0]["description"] == "Cliente corrigido"
    assert reopened["movement_ids"] == initial["movement_ids"]
    with pytest.raises(AnalysisEditConflictError):
        repository.apply(scope, command, expected_version=1, operation_id="batch", channel="pro")


def test_persistent_flags_never_restore_legacy_writer(database):
    from app.application.canonical_pro import CanonicalProService

    repository, scope, _, _, _, _ = database
    enroll(database)
    service = CanonicalProService(repository, None)
    assert service.resolve(scope.actor_id, scope.analysis_id) == scope
    with pytest.raises(AnalysisEditConflictError):
        service.edit(scope, [], expected_version=1, operation_id="disabled")


def enable(database):
    repository, scope, *_ = database
    with repository._connect() as conn:
        conn.execute(f'UPDATE {repository.p}canonical_analyses SET read_enabled=TRUE, edit_enabled=TRUE')


def test_existing_routes_sql_save_reopen_exports_and_ttl(database, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import httpx

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.application.received_statements import ReceivedStatements
    from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config, get_report_service
    from app.main import app
    from app.routers import canonical_pro
    from app.routers.received_statements import receipt_service
    from tests.test_convert_edits_http_real_api import _run_http_server

    repository, scope, content, _, batches, _ = database
    repository.test_access.activate_user_plan(user_id=scope.actor_id, plan_code="essencial")
    receipts = ReceivedStatements(repository.test_access.db, batches)
    receipts.receive(batches.get(scope.job_id))
    initial = enroll(database)
    enable(database)
    service = CanonicalProService(repository, LocalCanonicalObjects(tmp_path / "exports"))
    storage = TempAnalysisStorage(tmp_path / "legacy")
    metadata = storage.root_dir / scope.analysis_id / "analysis.json"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps(content), encoding="utf-8")
    storage.fence_canonical_analysis(scope.analysis_id)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: service)
    def configure(_):
        app.dependency_overrides[receipt_service] = lambda: receipts
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_access_control_service] = lambda: repository.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"})
    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=30,
            headers={"Authorization": f"Bearer {repository.test_user.token}"}) as client:
        payload = {"expected_version": 1, "operation_id": "new-http", "edits": [{
            "movement_id": initial["movement_ids"]["stable"], "date": "2026-04-02", "description": "Revisado", "debit": 2.75}]}
        path = f"/convert-edits/{scope.analysis_id}"
        saved = client.post(path, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == 2
        assert client.post(path, json=payload).json() == saved.json()
        assert client.get(path + "/operations/new-http").json() == saved.json()
        assert client.post(path, json={**payload, "operation_id": "other-editor"}).status_code == 409
        batch = client.get(f"/api/conversion-batches/{batches.get(scope.job_id).job.batch_id}")
        assert batch.status_code == 200, batch.text
        assert batch.json()["jobs"][0]["result"]["analysis"]["version"] == 2
        customer = client.get(f"/api/pro/receipts/clients/{scope.client_id}/workspace")
        assert customer.status_code == 200, customer.text
        assert customer.json()["jobs"][0]["result"]["analysis"]["movement_ids"] == initial["movement_ids"]
        assert customer.json()["jobs"][0]["result"]["analysis"]["preview_transactions"][0]["description"] == "Revisado"
        put = service.artifacts.objects.put
        def fail_export(key, data):
            raise OSError("injected object store outage")
        service.artifacts.objects.put = fail_export
        assert client.get(f"/convert-report/{scope.analysis_id}").status_code == 503
        assert repository.read(scope)["version"] == 2
        service.artifacts.objects.put = put
        assert client.get(f"/report/{scope.analysis_id}").status_code == 200
        for file_format in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{scope.analysis_id}?format={file_format}&expected_version=2")
            assert exported.status_code == 200, exported.text
            assert exported.headers["x-analysis-version"] == "2"
        assert client.get(f"/convert-report/{scope.analysis_id}?expected_version=1").status_code == 409
        batch_id = batches.get(scope.job_id).job.batch_id
        reviewed = client.post(f"/api/conversion-batches/{batch_id}/jobs/{scope.job_id}/review",
                              json={"operation_id": "review-http", "expected_version": 2})
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["version"] == 3
        stale_review = client.post(f"/api/conversion-batches/{batch_id}/jobs/{scope.job_id}/review",
                                   json={"operation_id": "stale-review", "expected_version": 2})
        assert stale_review.status_code == 409 and "sessão" in stale_review.json()["detail"]
        assert client.get(f"/api/conversion-batches/{batch_id}/export").status_code == 200
        with repository._connect() as conn:
            conn.execute(f"UPDATE {repository.p}canonical_analyses SET edit_enabled=FALSE")
        assert client.post(path, json=payload).json() == saved.json()  # A committed receipt survives the pause.
        assert client.post(path, json={**payload, "expected_version": 3, "operation_id": "off"}).status_code == 409
        with repository._connect() as conn:
            conn.execute(f"UPDATE {repository.p}conversion_batches SET expires_at=clock_timestamp()-interval '1 second'")
        assert client.get(f"/convert-report/{scope.analysis_id}").status_code == 404


@pytest.mark.parametrize("mode", ["adopt", "shadow", "interrupt"])
def test_completion_is_atomic_and_flags_are_persisted(database, tmp_path, mode, caplog):
    import json

    from psycopg.types.json import Jsonb

    from app.application.canonical_pro import CanonicalCompletion
    from app.application.storage_service import TempAnalysisStorage

    repository, scope, content, _, batches, _ = database
    record = batches.get(scope.job_id)
    storage = TempAnalysisStorage(tmp_path / "legacy")
    path = storage.root_dir / scope.analysis_id / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps(content), encoding="utf-8")
    with repository._connect() as conn:
        conn.execute(f'''INSERT INTO {repository.p}canonical_pro_cohorts
            (organization_id,ingestion_enabled,read_enabled,edit_enabled,shadow_enabled)
            VALUES (%s,%s,TRUE,TRUE,TRUE)''', (scope.organization_id, mode != "shadow"))
        identity = {**record.job.identity.__dict__, "plan_code": "essencial", "batch_conversion_enabled": True}
        conn.execute(f"UPDATE {repository.p}conversion_jobs SET status='running', identity=%s", (Jsonb(identity),))
    batches.canonical_completion = CanonicalCompletion(repository, storage)
    # Observe another connection before commit; a completed legacy result never leaks.
    completion = batches.canonical_completion
    def checked(conn, completed):
        completion(conn, completed)
        assert batches.get(scope.job_id).status.value == "running"
        if mode == "interrupt":
            raise RuntimeError("interrupted completion")
    batches.canonical_completion = checked
    if mode == "interrupt":
        with pytest.raises(RuntimeError, match="interrupted completion"):
            batches.mark_completed(scope.job_id, result=record.result)
        with repository._connect() as conn:
            assert conn.execute(f"SELECT count(*) AS n FROM {repository.p}canonical_analyses").fetchone()["n"] == 0
        assert batches.get(scope.job_id).status.value == "running"
        batches.canonical_completion = completion  # Retry the same fenced extraction, no reconversion.
    caplog.set_level("INFO")
    completed = batches.mark_completed(scope.job_id, result=record.result)
    if mode == "shadow":
        assert "authority" not in completed.result.payload
        assert "authority" not in json.loads(path.read_text(encoding="utf-8"))
        assert "differences=[]" in caplog.text
        with repository._connect() as conn:
            assert conn.execute(f"SELECT count(*) AS n FROM {repository.p}canonical_analyses").fetchone()["n"] == 0
        return
    assert completed.result.payload["authority"] == "postgres_v1"
    assert repository.read(scope)["version"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["authority"] == "canonical_pending"
    with pytest.raises(AnalysisEditConflictError):
        storage.apply_convert_edits(scope.analysis_id, [])
    from app.application.canonical_pro import CanonicalProService
    assert CanonicalProService(repository, None).read(scope)["canonical_edit_enabled"]


def test_free_never_calls_canonical_service(monkeypatch):
    from types import SimpleNamespace

    from app.routers import canonical_pro
    def forbidden():
        raise AssertionError("Free must not depend on SQL movements")
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", forbidden)
    assert canonical_pro.resolve_canonical(SimpleNamespace(identity_type="anonymous"), "free") is None
    assert canonical_pro.resolve_canonical(SimpleNamespace(identity_type="user", batch_conversion_enabled=False), "free") is None
