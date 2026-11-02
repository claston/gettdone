"""Real database, independent connections/processes and retained-lifetime checks."""
import multiprocessing
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.adapters.conversion.canonical_movements import PostgresMovementRepository
from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository
from app.application import AccessControlService
from app.application.canonical_movements import CanonicalRollout, MovementScope
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.models import TransactionRow
from app.application.pro_organization import OrganizationService

DSN = os.getenv("CANONICAL_MOVEMENTS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


def document():
    row = asdict(TransactionRow("2026-04-01", "Original", 10, "Outros", "unmatched",
                               transaction_id="stable", source_sequence=1, running_balance=110))
    now = datetime.now(timezone.utc)
    return {"analysis_id": "an_canonical", "file_type": "pdf", "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(), "created_at": now.isoformat(),
            "preview_transactions": [row], "report_transactions": [deepcopy(row)],
            "transactions_total": 1, "total_inflows": 10, "total_outflows": 0, "net_total": 10,
            "opening_balance": 100, "closing_balance": 110, "pdf_processing_metrics": {"page_count": 1}}


@pytest.fixture
def database(tmp_path):
    parsed = urlparse(DSN)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path in {"/ofx69_test", "/ofx24_test"}
    schema = f"ofx69_test_{uuid4().hex}"
    env = {**os.environ, "DATABASE_URL": DSN, "DATABASE_SCHEMA": schema}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="canonical-test",
                                  database_url=DSN, database_schema=schema)
    try:
        user = access.register_user(name="Owner", email="owner@example.test", password="strong-password")
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
        organization = OrganizationService(access.db)
        client = organization.save_client(user.user_id, name="Client", tax_id="12345678901")
        with psycopg.connect(DSN, row_factory=dict_row) as conn:
            org = conn.execute(f'SELECT id FROM "{schema}".pro_organizations').fetchone()["id"]
        batches = PostgresConversionBatchRepository(database_url=DSN, database_schema=schema)
        batch = ConversionBatch.create(identity_type="user", identity_id=user.user_id, files_count=1, idempotency_key="batch")
        job = ConversionJob.create(batch_id=batch.batch_id, identity=identity, idempotency_key="job",
                                   document=ConversionDocumentReference(storage_key="doc_" + "a" * 24, filename="test.csv",
                                                                        file_type="csv", size_bytes=1, sha256_hex="a" * 64))
        batches.create(batch=batch, jobs=[job])
        organization.save_context(user.user_id, job_id=job.job_id, client_id=client["id"], account_id=None,
                                  period_ids=[], expected_version=0)
        for transition in (batches.mark_uploaded, batches.mark_queued, batches.mark_running):
            transition(job.job_id)
        content = document()
        batches.mark_completed(job.job_id, result=ConversionJobResultReference(
            analysis_id=content["analysis_id"], payload={"analysis": content}))
        scope = MovementScope(user.user_id, org, job.job_id, content["analysis_id"], client["id"])
        repository = PostgresMovementRepository(DSN, schema=schema)
        repository.test_access, repository.test_user = access, user
        yield repository, scope, content, schema, batches, organization
    finally:
        access.close()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def enroll(database, content=None):
    repository, scope, original, *_ = database
    return repository.ingest(scope, content or original, currency="BRL", product="pro", new_analysis=True,
                             rollout=CanonicalRollout(True, frozenset({scope.organization_id})))


def edit(repository, scope, *, version=1, operation="edit", amount=2.75):
    return repository.apply(scope, [{"row_id": "stable", "action": "update", "date": "2026-04-02",
                                     "description": "Correção", "debit": amount}],
                            expected_version=version, operation_id=operation, channel="pro")


def test_commit_receipt_baseline_and_legacy_writer_fence(database):
    repository, scope, content, schema, batches, _ = database
    initial = enroll(database)
    assert initial["version"] == 1
    assert enroll(database) == initial
    result = edit(repository, scope)
    assert result["version"] == 2
    assert result["preview_transactions"][0]["amount"] == -2.75
    assert edit(repository, scope) == result
    with pytest.raises(AnalysisEditConflictError):
        edit(repository, scope, amount=5)
    with pytest.raises(AnalysisEditConflictError):
        edit(repository, scope, operation="stale")
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        movement = conn.execute(f'SELECT * FROM "{schema}".bank_movements').fetchone()
        assert str(movement["amount"]) == "-2.75"
        assert movement["baseline"]["description"] == "Original"
        assert movement["account_id"] is None
        assert movement["version"] == 2
        assert conn.execute(f'SELECT count(*) AS n FROM "{schema}".movement_revisions').fetchone()["n"] == 1
    with pytest.raises(Exception, match="SQL command adapter"):
        batches.update_analysis(scope.job_id, analysis={**content, "updated_at": "9999"})
    assert repository.read(scope)["version"] == 2
    # A historical retry returns its original committed response, even after v3.
    edit(repository, scope, version=2, operation="next", amount=6)
    assert edit(repository, scope) == result


def _race(dsn, schema, scope, start, output, operation):
    repository = PostgresMovementRepository(dsn, schema=schema)
    start.wait(20)
    try:
        result = edit(repository, scope, operation=operation)
        output.put(("saved", result["version"]))
    except AnalysisEditConflictError:
        output.put(("conflict", 409))


@pytest.mark.parametrize("same_operation", [False, True])
def test_two_processes_only_one_commit(database, same_operation):
    _, scope, _, schema, *_ = database
    enroll(database)
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    processes = [context.Process(target=_race, args=(DSN, schema, scope, start, output, "same" if same_operation else f"op-{i}"))
                 for i in range(2)]
    for process in processes:
        process.start()
    start.set()
    try:
        results = sorted(output.get(timeout=40) for _ in processes)
        assert results == ([("saved", 2), ("saved", 2)] if same_operation else [("conflict", 409), ("saved", 2)])
    finally:
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
            assert process.exitcode == 0


def test_scope_expiry_and_failure_before_commit(database):
    repository, scope, _, schema, *_ = database
    enroll(database)
    for altered in (replace(scope, actor_id="other"), replace(scope, organization_id="other"),
                    replace(scope, client_id="other"), replace(scope, job_id="other")):
        with pytest.raises(AnalysisNotFoundError):
            repository.read(altered)
    other = repository.test_access.register_user(name="Other", email="other@example.test", password="strong-password")
    other_client = OrganizationService(repository.test_access.db).save_client(other.user_id, name="Other client", tax_id=None)
    with psycopg.connect(DSN) as conn:
        other_org = conn.execute(f'SELECT id FROM "{schema}".pro_organizations WHERE owner_user_id = %s',
                                 (other.user_id,)).fetchone()[0]
    with pytest.raises(AnalysisNotFoundError):
        repository.read(replace(scope, actor_id=other.user_id, organization_id=other_org, client_id=other_client["id"]))
    def fail(stage):
        if stage == "before_commit":
            raise RuntimeError("simulated interruption")
    repository.failure_hook = fail
    with pytest.raises(RuntimeError, match="interruption"):
        edit(repository, scope)
    repository.failure_hook = None
    assert repository.read(scope)["version"] == 1
    assert repository.confirm(scope, "edit") is None
    with psycopg.connect(DSN) as conn:
        conn.execute(f'UPDATE "{schema}".canonical_analyses SET expires_at = clock_timestamp() - interval \'1 second\'')
    with pytest.raises(AnalysisNotFoundError):
        edit(repository, scope)
    deleted = []
    assert repository.purge_expired(delete_artifacts=lambda analysis_id: deleted.append(analysis_id)) == 1
    assert deleted == [scope.analysis_id]
    with psycopg.connect(DSN) as conn:
        for table in ("canonical_analyses", "bank_movements", "movement_revisions", "movement_operations", "movement_artifacts"):
            assert conn.execute(f'SELECT count(*) FROM "{schema}".{table}').fetchone()[0] == 0


def _crash(dsn, schema, scope, stage):
    def terminate(at):
        if at == stage:
            os._exit(79)
    repository = PostgresMovementRepository(dsn, schema=schema, failure_hook=terminate)
    edit(repository, scope)


@pytest.mark.parametrize("stage,version", [("before_commit", 1), ("after_commit", 2)])
def test_process_death_and_retry(database, stage, version):
    repository, scope, _, schema, *_ = database
    enroll(database)
    process = multiprocessing.get_context("spawn").Process(target=_crash, args=(DSN, schema, scope, stage))
    process.start()
    process.join(40)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 79
    assert repository.read(scope)["version"] == version
    assert edit(repository, scope)["version"] == 2
    with psycopg.connect(DSN) as conn:
        assert conn.execute(f'SELECT count(*) FROM "{schema}".movement_revisions').fetchone()[0] == 1


def test_expiry_during_operation_rolls_back(database):
    repository, scope, _, schema, *_ = database
    enroll(database)
    with psycopg.connect(DSN) as conn:
        conn.execute(f'UPDATE "{schema}".canonical_analyses SET expires_at = clock_timestamp() + interval \'1 second\'')
    def pause(stage):
        if stage == "before_commit":
            with psycopg.connect(DSN) as conn:
                conn.execute("SELECT pg_sleep(1.1)")
    repository.failure_hook = pause
    with pytest.raises(AnalysisNotFoundError):
        edit(repository, scope)
    with psycopg.connect(DSN) as conn:
        assert conn.execute(f'SELECT version FROM "{schema}".canonical_analyses').fetchone()[0] == 1
        assert conn.execute(f'SELECT count(*) FROM "{schema}".movement_operations').fetchone()[0] == 0


def test_history_immutable_and_multiconta_never_inherits_context(database):
    repository, scope, content, schema, batches, organization = database
    account = organization.save_account(scope.actor_id, client_id=scope.client_id, name="Document context")
    organization.save_context(scope.actor_id, job_id=scope.job_id, client_id=scope.client_id, account_id=account["id"],
                              period_ids=[], expected_version=1)
    for field in ("preview_transactions", "report_transactions"):
        row = content[field][0]
        row["statement_account"] = {"block_id": "block-a", "account_number": "111"}
        content[field].append({**deepcopy(row), "transaction_id": "stable-b", "source_sequence": 2,
                               "statement_account": {"block_id": "block-b", "account_number": "222"}})
    content["transactions_total"] = 2
    batches.update_analysis(scope.job_id, analysis=content)
    enroll(database)
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        rows = conn.execute(f'SELECT account_id, provenance FROM "{schema}".bank_movements ORDER BY ordinal').fetchall()
        assert [row["account_id"] for row in rows] == [None, None]
        assert [row["provenance"]["statement_account"]["block_id"] for row in rows] == ["block-a", "block-b"]
    # Annotation tests immutable evidence without invoking account balance rules.
    repository.apply(scope, [{"row_id": "stable", "action": "annotate", "review_regions": []}],
                     expected_version=1, operation_id="annotation", channel="pro")
    for sql in (f'UPDATE "{schema}".bank_movements SET baseline = \'{{}}\'',
                f'UPDATE "{schema}".movement_operations SET channel = \'changed\'',
                f'DELETE FROM "{schema}".movement_operations'):
        with psycopg.connect(DSN) as conn, pytest.raises(psycopg.Error, match="immutable|append-only"):
            conn.execute(sql)


def test_artifact_failure_never_loses_edit_or_serves_previous_version(database, tmp_path):
    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_artifacts import CanonicalArtifactService

    repository, scope, *_ = database
    enroll(database)
    objects = LocalCanonicalObjects(tmp_path / "objects")
    artifacts = CanonicalArtifactService(repository, objects)
    first = artifacts.generate(scope)
    old_bytes = artifacts.download(scope, "converted.csv")
    edit(repository, scope)
    assert repository.artifact(scope) is None
    original_put = objects.put
    def fail(key, data):
        if key.endswith("converted.xlsx"):
            raise OSError("simulated storage failure")
        original_put(key, data)
    objects.put = fail
    with pytest.raises(OSError, match="storage failure"):
        artifacts.download(scope, "converted.csv")
    assert repository.read(scope)["version"] == 2
    assert repository.artifact(scope) is None
    objects.put = original_put
    new_bytes = artifacts.download(scope, "converted.csv")
    assert new_bytes != old_bytes
    assert b"-2.75" in new_bytes or b"-2,75" in new_bytes
    assert repository.artifact(scope)["version"] == 2
    assert first["version"] == 1


def test_stale_generation_cannot_publish(database, tmp_path):
    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_artifacts import CanonicalArtifactService

    repository, scope, *_ = database
    enroll(database)
    objects = LocalCanonicalObjects(tmp_path / "objects")
    original_put = objects.put
    changed = []
    def concurrent_edit(key, data):
        original_put(key, data)
        if not changed:
            changed.append(edit(repository, scope))
    objects.put = concurrent_edit
    with pytest.raises(AnalysisEditConflictError):
        CanonicalArtifactService(repository, objects).generate(scope)
    assert repository.artifact(scope) is None


def test_legacy_adapter_timestamp_and_missing_version(database):
    from app.adapters.conversion.canonical_review import CanonicalReviewAdapter

    repository, scope, content, *_ = database
    enroll(database)
    adapter = CanonicalReviewAdapter(repository, scope)
    command = dict(analysis_id=scope.analysis_id, edits=[{"row_id": "stable", "action": "delete"}],
                   operation_id="delete", expected_updated_at=content["updated_at"])
    result = adapter.apply_convert_edits(**command)
    assert result["version"] == 2
    assert adapter.apply_convert_edits(**command) == result
    assert adapter.get_convert_edit_operation(scope.analysis_id, "delete") == result
    with pytest.raises(AnalysisEditConflictError):
        adapter.apply_convert_edits(scope.analysis_id, [], operation_id="missing-version")


def test_pending_fence_resumes_without_legacy_fallback(database, tmp_path):
    import json

    from app.application.canonical_ingestion import CanonicalIngestionService
    from app.application.storage_service import TempAnalysisStorage

    repository, scope, content, *_ = database
    storage = TempAnalysisStorage(tmp_path / "legacy")
    path = storage.root_dir / scope.analysis_id / "analysis.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(content), encoding="utf-8")
    rollout = CanonicalRollout(True, frozenset({scope.organization_id}))
    coordinator = CanonicalIngestionService(repository, storage, rollout=rollout)
    original_ingest = repository.ingest
    def fail(*args, **kwargs):
        raise RuntimeError("interrupted adoption")
    repository.ingest = fail
    with pytest.raises(RuntimeError, match="adoption"):
        coordinator.ingest(scope, currency="BRL", product="pro", new_analysis=True)
    with pytest.raises(AnalysisEditConflictError):
        storage.apply_convert_edits(scope.analysis_id, [])
    repository.ingest = original_ingest
    first = coordinator.ingest(scope, currency="BRL", product="pro", new_analysis=True)
    assert coordinator.ingest(scope, currency="BRL", product="pro", new_analysis=True) == first
    edit(repository, scope)
    with pytest.raises(AnalysisEditConflictError):
        storage.get_convert_report_path(scope.analysis_id, "csv")
    # Turning off adoption does not switch an existing document's authority.
    coordinator.rollout = CanonicalRollout()
    assert repository.read(scope)["version"] == 2
    with pytest.raises(ValueError, match="disabled"):
        coordinator.ingest(scope, currency="BRL", product="pro", new_analysis=True)


def test_cascade_retains_artifact_cleanup_work_and_retries(database):
    repository, scope, _, schema, batches, _ = database
    enroll(database)
    edit(repository, scope)
    batches.delete(scope.job_id)
    with psycopg.connect(DSN) as conn:
        assert conn.execute(f'SELECT count(*) FROM "{schema}".bank_movements').fetchone()[0] == 0
        assert conn.execute(f'SELECT analysis_id FROM "{schema}".canonical_artifact_cleanup').fetchone()[0] == scope.analysis_id
    def fail(_):
        raise OSError("storage offline")
    with pytest.raises(OSError):
        repository.purge_expired(delete_artifacts=fail)
    deleted = []
    assert repository.purge_expired(delete_artifacts=deleted.append) == 1
    assert deleted == [scope.analysis_id]
    assert repository.purge_expired(delete_artifacts=deleted.append) == 0


def test_upgrade_downgrade_reupgrade_and_block_unsafe_rollback(database):
    repository, scope, _, schema, *_ = database
    env = {**os.environ, "DATABASE_URL": DSN, "DATABASE_SCHEMA": schema}
    def migrate(direction, revision):
        return subprocess.run([sys.executable, "-m", "alembic", direction, revision],
                              cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    result = migrate("downgrade", "20261101_01")
    assert result.returncode == 0, result.stderr
    result = migrate("upgrade", "head")
    assert result.returncode == 0, result.stderr
    enroll(database)
    edit(repository, scope)
    result = migrate("downgrade", "20261101_01")
    assert result.returncode != 0 and "cannot be downgraded" in result.stderr
    assert repository.read(scope)["version"] == 2


def test_existing_http_contract_with_real_postgres_returns_409_and_receipt(database):
    import httpx

    from app.adapters.conversion.canonical_review import CanonicalReviewAdapter
    from app.application import ReportService
    from app.dependencies import get_access_control_service, get_report_service
    from app.main import app
    from tests.test_convert_edits_http_real_api import _run_http_server

    repository, scope, content, *_ = database
    enroll(database)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repository.test_access
        app.dependency_overrides[get_report_service] = lambda: ReportService(CanonicalReviewAdapter(repository, scope))
    with _run_http_server(configure) as base_url:
        url = f"{base_url}/convert-edits/{scope.analysis_id}"
        params = {"user_token": repository.test_user.token}
        payload = {"operation_id": "http-delete", "expected_updated_at": content["updated_at"],
                   "edits": [{"action": "delete", "row_id": "stable"}]}
        saved = httpx.post(url, params=params, json=payload)
        replay = httpx.post(url, params=params, json=payload)
        receipt = httpx.get(f"{url}/operations/http-delete", params=params)
        stale = httpx.post(url, params=params, json={**payload, "operation_id": "stale"})
        wrong_content = httpx.post(url, params=params, json={**payload, "edits": []})
    assert saved.status_code == replay.status_code == receipt.status_code == 200, saved.text
    assert saved.json() == replay.json() == receipt.json()
    assert stale.status_code == wrong_content.status_code == 409


@pytest.mark.parametrize("status", ["internal_transfer", "reversal", "possible_duplicate"])
def test_financial_edit_invalidates_previous_heuristic_groups(database, status):
    repository, scope, content, schema, batches, _ = database
    for field in ("preview_transactions", "report_transactions"):
        content[field][0]["reconciliation_status"] = status
        content[field].append({**deepcopy(content[field][0]), "transaction_id": "peer", "amount": -10, "source_sequence": 2})
    content["transactions_total"] = 2
    batches.update_analysis(scope.job_id, analysis=content)
    enroll(database)
    edit(repository, scope)
    rows = repository.read(scope)["preview_transactions"]
    assert [row["reconciliation_status"] for row in rows] == ["unmatched", "unmatched"]
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        baselines = conn.execute(f'SELECT baseline FROM "{schema}".bank_movements').fetchall()
        assert all(row["baseline"]["reconciliation_status"] == status for row in baselines)


def test_insert_delete_restore_and_review_have_stable_ids_and_audit(database):
    repository, scope, _, schema, *_ = database
    original = enroll(database)
    reviewed = repository.apply(scope, [], expected_version=1, operation_id="review", channel="pro", mark_reviewed=True)
    assert reviewed["version"] == 2
    assert repository.read(scope)["movement_review_states"] == {"stable": "reviewed"}
    command = [{"action": "insert", "anchor_transaction_id": "stable", "date": "2026-04-01",
                "description": "Original", "credit": 10}]
    inserted = repository.apply(scope, command, expected_version=2, operation_id="insert", channel="pro")
    assert repository.apply(scope, command, expected_version=2, operation_id="insert", channel="pro") == inserted
    rows = inserted["preview_transactions"]
    assert len(rows) == 2  # Legitimate repeated date/description/value remains.
    assert inserted["movement_ids"]["stable"] == original["movement_ids"]["stable"]
    assert all(state == "pending" for state in repository.read(scope)["movement_review_states"].values())
    new_id = next(row["transaction_id"] for row in rows if row["transaction_id"] != "stable")
    for version, action in ((3, "delete"), (4, "restore")):
        repository.apply(scope, [{"action": action, "row_id": new_id, "date": "2026-04-01", "description": "Original", "credit": 10}],
                         expected_version=version, operation_id=action, channel="pro")
    assert repository.read(scope)["version"] == 5
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        manual = conn.execute(f'SELECT * FROM "{schema}".bank_movements WHERE legacy_id = %s', (new_id,)).fetchone()
        assert manual["baseline"] is None and manual["provenance"]["origin"] == "manual"
        assert not manual["is_deleted"] and manual["version"] == 3
        assert conn.execute(f'SELECT count(*) AS n FROM "{schema}".movement_operations').fetchone()["n"] == 4


def test_exporter_financial_parity_uses_ofx67_normalizers(database, tmp_path, monkeypatch):
    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_artifacts import CanonicalArtifactService
    from app.application.review_commands import ReviewCommandResult, ReviewCommandService
    from app.application.storage_service import TempAnalysisStorage
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from free_converter_guard import normalize_ofx, snapshot_csv, snapshot_workbook

    repository, scope, content, *_ = database
    enroll(database)
    legacy = TempAnalysisStorage(tmp_path / "legacy-export")
    prepared = ReviewCommandService().prepare_export(deepcopy(content))
    legacy._publish_review_artifacts(legacy.root_dir, content, ReviewCommandResult(
        report_rows=prepared.report_rows, preview_rows=prepared.report_rows, effective_overrides=prepared.effective_overrides))
    service = CanonicalArtifactService(repository, LocalCanonicalObjects(tmp_path / "objects"))
    for name in ("converted.csv", "converted.xlsx", "report.xlsx", "converted.ofx"):
        before = legacy.root_dir / name
        if not before.exists():
            continue
        after = tmp_path / name
        after.write_bytes(service.download(scope, name))
        if name.endswith("xlsx"):
            assert snapshot_workbook(before) == snapshot_workbook(after)
        elif name.endswith("csv"):
            assert snapshot_csv(before.read_text(encoding="utf-8")) == snapshot_csv(after.read_text(encoding="utf-8"))
        else:
            assert normalize_ofx(before.read_text(encoding="utf-8"), empty=False) == normalize_ofx(
                after.read_text(encoding="utf-8"), empty=False)


def test_download_revalidates_expiry_after_object_read(database, tmp_path):
    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_artifacts import CanonicalArtifactService

    repository, scope, _, schema, *_ = database
    enroll(database)
    objects = LocalCanonicalObjects(tmp_path / "objects")
    service = CanonicalArtifactService(repository, objects)
    service.generate(scope)
    original = objects.get
    def expire(key):
        data = original(key)
        with psycopg.connect(DSN) as conn:
            conn.execute(f'UPDATE "{schema}".canonical_analyses SET expires_at = clock_timestamp()')
        return data
    objects.get = expire
    with pytest.raises(AnalysisNotFoundError):
        service.download(scope, "converted.csv")


def test_cleanup_waits_for_inflight_export_and_removes_unpublished_objects(database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application.canonical_artifacts import CanonicalArtifactService

    repository, scope, _, schema, *_ = database
    enroll(database)
    objects = LocalCanonicalObjects(tmp_path / "objects")
    started, release = Event(), Event()
    original_put = objects.put
    def pause(key, data):
        original_put(key, data)
        if not started.is_set():
            started.set()
            assert release.wait(10)
    objects.put = pause
    with ThreadPoolExecutor(max_workers=2) as workers:
        generating = workers.submit(CanonicalArtifactService(repository, objects).generate, scope)
        try:
            assert started.wait(10)
            with psycopg.connect(DSN) as conn:
                conn.execute(f'UPDATE "{schema}".canonical_analyses SET expires_at = clock_timestamp()')
            cleaning = workers.submit(repository.purge_expired, delete_artifacts=objects.delete_analysis)
            with pytest.raises(TimeoutError):
                cleaning.result(timeout=0.1)
        finally:
            release.set()
        with pytest.raises(AnalysisNotFoundError):
            generating.result(timeout=10)
        assert cleaning.result(timeout=10) == 1
    assert not (objects.root / "canonical" / scope.analysis_id).exists()


def test_pending_client_cannot_be_reassigned_by_a_new_legacy_context(database):
    repository, scope, content, schema, _, organization = database
    with psycopg.connect(DSN) as conn:
        conn.execute(f'DELETE FROM "{schema}".pro_conversion_contexts WHERE job_id = %s', (scope.job_id,))
    pending = replace(scope, client_id=None)
    repository.ingest(pending, content, currency="BRL", product="pro", new_analysis=True,
                      rollout=CanonicalRollout(True, frozenset({scope.organization_id})))
    with pytest.raises(psycopg.Error, match="versioned command"):
        organization.save_context(scope.actor_id, job_id=scope.job_id, client_id=scope.client_id, account_id=None,
                                  period_ids=[], expected_version=0)
    assert repository.read(pending)["version"] == 1
