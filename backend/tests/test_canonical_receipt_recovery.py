"""Late client confirmation adopts the retained, current statement exactly once."""
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from test_canonical_movements_postgres import DSN
from test_financial_center import add_document
from test_financial_center import database as paid_database

from app.application.canonical_pro import CanonicalCompletion
from app.application.canonical_receipt_recovery import CanonicalReceiptRecovery
from app.application.financial_center import FinancialCenterService
from app.application.received_statements import ReceivedStatements
from app.application.storage_service import TempAnalysisStorage

pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")
database = paid_database


def setup_recovery(database, tmp_path):
    repo, scope, *_ = database
    storage = TempAnalysisStorage(tmp_path / "sources")
    with repo._connect() as conn:
        # The unrelated seed document has no legacy storage in these fixtures.
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (scope.job_id,))
        conn.execute(f'''INSERT INTO {repo.p}canonical_pro_cohorts
            (organization_id,ingestion_enabled,read_enabled,edit_enabled,ui_enabled)
            VALUES (%s,TRUE,TRUE,TRUE,TRUE)''', (scope.organization_id,))
    recovery = CanonicalReceiptRecovery(repo, storage)
    receipts = ReceivedStatements(repo.test_access.db, database[4], recovery=recovery)
    return storage, recovery, receipts


def unidentified(database, storage, suffix, *, rows=None):
    content = deepcopy(database[2])
    content["analysis_id"] = "an_" + suffix
    if rows is not None:
        content["preview_transactions"] = rows
        content["report_transactions"] = deepcopy(rows)
    path = storage.root_dir / content["analysis_id"] / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps(content), encoding="utf-8")
    record, _ = add_document(database, suffix=suffix, rows=rows, context=False,
        source={"confidence": "missing"}, completion=CanonicalCompletion(database[0], storage))
    return record, content, path


def test_three_confirmations_publish_all_months_once_and_preserve_source(database, tmp_path):
    repo, scope, original, _, batches, _ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    records = []
    for index, month in enumerate((7, 8, 9)):
        rows = [dict(original["preview_transactions"][0], date=f"2026-{month:02}-03", source_page=2)]
        record, content, _ = unidentified(database, storage, f"manual{index}", rows=rows)
        records.append(record)
        saved = receipts.confirm(scope.actor_id, record.job.job_id, expected_version=0,
                                 name="Cliente fictício", client_id=scope.client_id)
        assert saved["client_id"] == scope.client_id
        current = batches.get(record.job.job_id)
        assert current.result.payload["authority"] == "postgres_v1"
        assert current.result.payload["analysis"]["preview_transactions"] == rows
        assert current.expires_at == record.expires_at
        central = FinancialCenterService(repo).month(scope.actor_id, scope.client_id, month=date(2026, month, 1))
        assert len(central["movements"]) == 1
        assert central["movements"][0]["source"]["analysis_id"] == content["analysis_id"]
        assert central["movements"][0]["source"]["page"] == 2
    receipts.sync(scope.actor_id)
    receipts.sync(scope.actor_id)
    with repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {repo.p}bank_movements").fetchone()["n"] == 3


def test_refresh_recovers_already_confirmed_statement_preserving_edits(database, tmp_path):
    from app.application.errors import AnalysisEditConflictError
    from app.application.review_commands import ReviewCommandService

    repo, scope, _, _, batches, _ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "alreadyconfirmed")
    legacy = ReceivedStatements(repo.test_access.db, batches)
    legacy.confirm(scope.actor_id, record.job.job_id, expected_version=0,
                   name="Cliente fictício", client_id=scope.client_id)
    command = [{"row_id": "stable", "date": "2026-04-02", "description": "Correção preservada", "debit": 12.75}]
    before = content["updated_at"]
    ReviewCommandService().apply(content, command, expected_updated_at=before, operation_id="legacy-edit")
    path.write_text(json.dumps(content), encoding="utf-8")
    # Deliberately leave the old job projection stale: storage owns legacy edits.
    receipts.sync(scope.actor_id)
    imported_scope = replace(scope, job_id=record.job.job_id, analysis_id=content["analysis_id"])
    snapshot = repo.read(imported_scope)
    assert snapshot["preview_transactions"] == content["preview_transactions"]
    assert snapshot["updated_at"] == content["updated_at"]
    assert repo.confirm(imported_scope, "legacy-edit")["updated_at"] == content["updated_at"]
    replay = repo.apply(imported_scope, command, expected_updated_at=before, operation_id="legacy-edit", channel="pro")
    assert replay["version"] == 1
    with pytest.raises(AnalysisEditConflictError):
        repo.apply(imported_scope, [], mark_reviewed=True, expected_version=1, operation_id="legacy-edit", channel="pro")
    with pytest.raises(AnalysisEditConflictError):
        repo.apply(imported_scope, [dict(command[0], debit=99)], expected_updated_at=before,
                   operation_id="legacy-edit", channel="pro")
    with repo._connect() as conn:
        baseline = conn.execute(f"SELECT baseline FROM {repo.p}canonical_analyses WHERE analysis_id=%s",
                                (content["analysis_id"],)).fetchone()["baseline"]
        assert baseline["edit_operations"] == content["edit_operations"]
    central = FinancialCenterService(repo).month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))
    assert central["movements"][0]["amount"] == "-12.75"
    revised = repo.apply(imported_scope, [{"row_id": "stable", "date": "2026-04-03", "description": "Nova revisão", "credit": 20}],
                         expected_version=1, operation_id="after-import", channel="pro")
    assert revised["version"] == 2
    with pytest.raises(AnalysisEditConflictError):
        repo.confirm(imported_scope, "legacy-edit")


@pytest.mark.parametrize("stage", ["recovery_fenced", "recovery_before_commit"])
def test_interrupted_import_keeps_identity_and_retries_without_duplicates(database, tmp_path, stage):
    from app.application.errors import AnalysisEditConflictError
    from app.application.received_statements import ReceiptRecoveryPending

    repo, scope, _, _, batches, organization = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, _, path = unidentified(database, storage, "interrupted")
    def fail(actual):
        if actual == stage:
            raise OSError("injected interruption")
    repo.failure_hook = fail
    with pytest.raises(ReceiptRecoveryPending):
        receipts.confirm(scope.actor_id, record.job.job_id, expected_version=0,
                         name="Cliente", client_id=scope.client_id)
    assert organization.contexts(scope.actor_id, [record.job.job_id])[record.job.job_id]["client_id"] == scope.client_id
    assert json.loads(path.read_text(encoding="utf-8"))["authority"] == "canonical_pending"
    assert "authority" not in batches.get(record.job.job_id).result.payload
    with pytest.raises(AnalysisEditConflictError):
        storage.apply_convert_edits(record.result.analysis_id, [], operation_id="unsafe-fallback")
    with repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {repo.p}bank_movements").fetchone()["n"] == 0
    repo.failure_hook = None
    receipts.sync(scope.actor_id)
    receipts.sync(scope.actor_id)
    assert batches.get(record.job.job_id).result.payload["authority"] == "postgres_v1"
    with repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {repo.p}bank_movements").fetchone()["n"] == 1


@pytest.mark.parametrize("flag", ["ingestion_enabled", "read_enabled", "ui_enabled"])
def test_disabled_cohort_never_fences_or_enables_existing_sources(database, tmp_path, flag):
    repo, scope, _, _, batches, _ = database
    storage, recovery, receipts = setup_recovery(database, tmp_path)
    record, _, path = unidentified(database, storage, "disabled")
    before = path.read_bytes()
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}canonical_pro_cohorts SET {flag}=FALSE")
    receipts.confirm(scope.actor_id, record.job.job_id, expected_version=0, name="Cliente", client_id=scope.client_id)
    assert recovery.recover(scope.actor_id, record.job.job_id) == "disabled"
    assert path.read_bytes() == before
    assert "authority" not in batches.get(record.job.job_id).result.payload


@pytest.mark.parametrize("invalid", ["divergent_rows", "missing_id", "empty"])
def test_invalid_candidate_is_rejected_before_any_storage_fence(database, tmp_path, invalid):
    from app.application.received_statements import ReceiptRecoveryPending

    repo, scope, *_ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "invalid")
    if invalid == "divergent_rows":
        content["report_transactions"][0]["amount"] = 99
    elif invalid == "missing_id":
        for key in ("preview_transactions", "report_transactions"):
            content[key][0].pop("transaction_id")
    else:
        content["preview_transactions"] = content["report_transactions"] = []
    path.write_text(json.dumps(content), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ReceiptRecoveryPending):
        receipts.confirm(scope.actor_id, record.job.job_id, expected_version=0, name="Cliente", client_id=scope.client_id)
    assert path.read_bytes() == before
    with repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {repo.p}canonical_analyses").fetchone()["n"] == 0


def test_owner_expiry_and_plan_are_rechecked_before_storage_is_touched(database, tmp_path):
    from app.application.errors import AnalysisAccessDeniedError

    repo, scope, _, _, batches, _ = database
    storage, recovery, _ = setup_recovery(database, tmp_path)
    record, _, path = unidentified(database, storage, "access")
    ReceivedStatements(repo.test_access.db, batches).confirm(scope.actor_id, record.job.job_id, expected_version=0,
                                                            name="Cliente", client_id=scope.client_id)
    before = path.read_bytes()
    assert recovery.recover("another-owner", record.job.job_id) == "ineligible"
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}user_plan_subscriptions SET status='cancelled' WHERE user_id=%s", (scope.actor_id,))
    with pytest.raises(AnalysisAccessDeniedError):
        recovery.recover(scope.actor_id, record.job.job_id)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (record.job.job_id,))
    assert recovery.recover(scope.actor_id, record.job.job_id) == "ineligible"
    assert path.read_bytes() == before


def test_s3_recovery_uses_latest_remote_state_and_preserves_delete_insert_and_annotations(database, tmp_path):
    from app.application.review_commands import ReviewCommandService
    from app.application.s3_analysis_storage import S3AnalysisStorage
    from tests.test_s3_analysis_storage import FakeS3Client

    repo, scope, _, _, batches, _ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "remote")
    ReceivedStatements(repo.test_access.db, batches).confirm(scope.actor_id, record.job.job_id, expected_version=0,
                                                            name="Cliente", client_id=scope.client_id)
    client = FakeS3Client()
    key = ("private", f"conversion/results/{content['analysis_id']}/analysis.json")
    client.objects[key] = path.read_bytes()
    remote = S3AnalysisStorage(root_dir=tmp_path / "api", bucket="private", s3_client=client)
    remote.read_canonical_candidate(content["analysis_id"])
    ReviewCommandService().apply(content, [
        {"row_id": "stable", "action": "delete"},
        {"action": "insert", "date": "2026-04-03", "description": "Movimento incluído", "credit": 7.25},
    ], operation_id="remote-edit")
    client.objects[key] = json.dumps(content).encode()
    receipts.recovery = CanonicalReceiptRecovery(repo, remote)
    receipts.sync(scope.actor_id)
    imported = batches.get(record.job.job_id).result.payload["analysis"]
    assert imported["preview_transactions"] == content["preview_transactions"]
    assert json.loads(client.objects[key])["authority"] == "canonical_pending"
    result = FinancialCenterService(repo).month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))
    assert [row["amount"] for row in result["movements"]] == ["7.25"]
    assert result["movements"][0]["source"]["page"] is None


def test_recovery_waits_for_other_api_publication_and_retains_its_edit(database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from app.adapters.conversion.canonical_movements import PostgresMovementRepository
    from app.application.review_commands import ReviewCommandService

    repo, scope, _, _, batches, _ = database
    storage, recovery, _ = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "concurrent")
    ReceivedStatements(repo.test_access.db, batches).confirm(scope.actor_id, record.job.job_id, expected_version=0,
                                                            name="Cliente", client_id=scope.client_id)
    other_api = PostgresMovementRepository(repo.database_url, schema=repo.schema)
    waiting = Event()
    def recover():
        waiting.set()
        return recovery.recover(scope.actor_id, record.job.job_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with other_api.legacy_guard(content["analysis_id"]):
            future = pool.submit(recover)
            assert waiting.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            ReviewCommandService().apply(content, [{"row_id": "stable", "date": "2026-04-01",
                "description": "Edição simultânea", "credit": 21}], operation_id="concurrent-edit")
            path.write_text(json.dumps(content), encoding="utf-8")
        assert future.result(timeout=15) == "recovered"
    assert batches.get(record.job.job_id).result.payload["analysis"]["preview_transactions"] == content["preview_transactions"]


@contextmanager
def recovery_http(database, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_pro import CanonicalProService
    from app.application.report_service import ReportService
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_canonical_receipt_recovery,
        get_conversion_batch_document_store,
        get_conversion_batch_service,
        get_conversion_runtime_config,
        get_report_service,
    )
    from app.main import app
    from app.routers import canonical_pro
    from app.routers.financial_center import financial_service
    from tests.http_runtime import LoopbackClient
    from tests.test_convert_edits_http_real_api import _run_http_server

    repo, scope, _, _, batches, _ = database
    storage, recovery, _ = setup_recovery(database, tmp_path)
    canonical = CanonicalProService(repo, LocalCanonicalObjects(tmp_path / "exports"))
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: canonical)
    def configure(_):
        from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[get_canonical_receipt_recovery] = lambda: recovery
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_conversion_batch_document_store] = lambda: None
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_async_conversion_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[financial_service] = lambda: FinancialCenterService(repo)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"})
    with _run_http_server(configure) as base, LoopbackClient(base_url=base, timeout=30, headers={
        "Authorization": f"Bearer {repo.test_user.token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
    }) as client:
        yield client, storage


@pytest.mark.real_http
def test_http_edit_confirm_central_replay_export_and_negative_paths(database, tmp_path, monkeypatch):
    repo, scope, _, _, batches, _ = database
    with recovery_http(database, tmp_path, monkeypatch) as (client, storage):
        record, content, _ = unidentified(database, storage, "http")
        storage.set_convert_owner(content["analysis_id"], "user", scope.actor_id)
        edit_path = f"/api/pro/convert-edits/{content['analysis_id']}"
        edit = {"expected_updated_at": content["updated_at"], "operation_id": "http-before",
                "job_id": record.job.job_id, "batch_id": record.job.batch_id,
                "edits": [{"row_id": "stable", "date": "2026-04-02", "description": "Correção HTTP", "debit": 19.25}]}
        saved = client.post(edit_path, json=edit)
        assert saved.status_code == 200, saved.text
        confirmation = client.post(f"/api/pro/receipts/documents/{record.job.job_id}/confirm", json={
            "expected_version": 0, "name": "Cliente", "client_id": scope.client_id})
        assert confirmation.status_code == 200, confirmation.text
        central = client.get(f"/api/pro/financial-center/clients/{scope.client_id}")
        assert central.status_code == 200, central.text
        assert central.json()["month"] == "2026-04"
        assert central.json()["movements"][0]["amount"] == "-19.25"
        assert client.post(edit_path, json=edit).json()["version"] == 1
        assert client.get(edit_path + "/operations/http-before").status_code == 200
        assert client.get(f"/api/pro/receipts/clients/{scope.client_id}/workspace").status_code == 200
        assert client.get(f"/report/{content['analysis_id']}").status_code == 200
        for extension in ("ofx", "csv", "xlsx"):
            exported = client.get(f"/convert-report/{content['analysis_id']}", params={"format": extension})
            assert exported.status_code == 200, exported.text
            assert exported.headers["x-analysis-version"] == "1"
        assert client.get("/report/an_missing").status_code == 404
        assert client.post("/analyze", files={"file": ("test.csv", b"date,amount\n2026-04-01,10\n", "text/csv")}).status_code == 405
        assert client.post("/api/pro/receipts/refresh").status_code == 200
        assert batches.get(record.job.job_id).result.payload["analysis"]["version"] == 1


@pytest.mark.real_http
def test_http_interruption_reports_saved_identity_and_refresh_recovers(database, tmp_path, monkeypatch):
    repo, scope, *_ = database
    with recovery_http(database, tmp_path, monkeypatch) as (client, storage):
        record, _, _ = unidentified(database, storage, "httpfailure")
        def fail(stage):
            if stage == "recovery_before_commit":
                raise OSError("injected private storage detail")
        repo.failure_hook = fail
        response = client.post(f"/api/pro/receipts/documents/{record.job.job_id}/confirm", json={
            "expected_version": 0, "name": "Cliente", "client_id": scope.client_id})
        assert response.status_code == 503, response.text
        assert "identificação está salva" in response.json()["detail"]
        assert "private storage detail" not in response.text
        assert client.get("/api/pro/receipts").json()["pending_count"] == 0
        repo.failure_hook = None
        assert client.post("/api/pro/receipts/refresh").status_code == 200
        assert len(client.get(f"/api/pro/financial-center/clients/{scope.client_id}").json()["movements"]) == 1


def test_recovery_rollout_is_explicit_and_disabled_by_default(monkeypatch):
    from types import SimpleNamespace

    from app import dependencies

    monkeypatch.delenv("CANONICAL_PRO_RECOVERY_ENABLED", raising=False)
    monkeypatch.setattr(dependencies, "get_canonical_pro_service", lambda: SimpleNamespace(repository="repo"))
    monkeypatch.setattr(dependencies, "get_async_conversion_report_service", lambda: SimpleNamespace(storage="storage"))
    assert dependencies.get_canonical_receipt_recovery() is None
    monkeypatch.setenv("CANONICAL_PRO_RECOVERY_ENABLED", "true")
    recovery = dependencies.get_canonical_receipt_recovery()
    assert recovery.repository == "repo" and recovery.storage == "storage"


@pytest.mark.parametrize("edited_after_review", [False, True])
def test_review_status_and_shorter_receipt_retention_are_preserved(database, tmp_path, edited_after_review):
    from datetime import datetime, timedelta, timezone

    from app.application.review_commands import ReviewCommandService

    repo, scope, _, _, batches, _ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "reviewed")
    ReceivedStatements(repo.test_access.db, batches).confirm(scope.actor_id, record.job.job_id, expected_version=0,
                                                            name="Cliente", client_id=scope.client_id)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    batches.mark_reviewed(record.job.job_id, reviewed_at=reviewed_at)
    if edited_after_review:
        ReviewCommandService().apply(content, [{"row_id": "stable", "date": "2026-04-01", "description": "Alterado", "credit": 11}])
        path.write_text(json.dumps(content), encoding="utf-8")
    short_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}pro_received_statements SET expires_at=%s WHERE job_id=%s",
                     (short_expiry.isoformat(), record.job.job_id))
    receipts.recovery.recover(scope.actor_id, record.job.job_id)
    snapshot = batches.get(record.job.job_id).result.payload["analysis"]
    assert snapshot.get("reviewed_at") == (None if edited_after_review else reviewed_at)
    assert set(snapshot["movement_review_states"].values()) == ({"pending"} if edited_after_review else {"reviewed"})
    assert datetime.fromisoformat(snapshot["expires_at"]) == short_expiry


def test_confirmed_empty_statement_can_be_imported_without_inventing_movements(database, tmp_path):
    _, scope, *_ = database
    storage, _, receipts = setup_recovery(database, tmp_path)
    record, content, path = unidentified(database, storage, "empty", rows=[])
    content["pdf_processing_metrics"]["statement_no_activity"] = 1
    path.write_text(json.dumps(content), encoding="utf-8")
    receipts.confirm(scope.actor_id, record.job.job_id, expected_version=0, name="Cliente", client_id=scope.client_id)
    snapshot = database[4].get(record.job.job_id).result.payload["analysis"]
    assert snapshot["authority"] == "postgres_v1" and snapshot["preview_transactions"] == []
