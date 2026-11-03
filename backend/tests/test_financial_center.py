"""Financial center reads and prerequisites against disposable PostgreSQL."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest
from test_canonical_movements_postgres import DSN
from test_canonical_pro import database as paid_database
from test_canonical_pro import enable

from app.application.canonical_movements import CanonicalRollout, MovementScope
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob

database = paid_database

pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


def add_document(database, *, suffix, rows=None, context=True, source=None, completion=None, digest=None):
    repo, scope, original, _, batches, organization = database
    identity = repo.test_access.resolve_identity(anonymous_fingerprint=None, user_token=repo.test_user.token)
    batch = ConversionBatch.create(identity_type="user", identity_id=scope.actor_id, files_count=1, idempotency_key=suffix)
    document = replace(batches.get(scope.job_id).job.document, sha256_hex=digest or (suffix[0] * 64))
    job = ConversionJob.create(batch_id=batch.batch_id, identity=identity, idempotency_key=suffix, document=document)
    batches.create(batch=batch, jobs=[job])
    if context:
        organization.save_context(scope.actor_id, job_id=job.job_id, client_id=scope.client_id,
                                  account_id=None, period_ids=[], expected_version=0)
    for transition in (batches.mark_uploaded, batches.mark_queued, batches.mark_running):
        transition(job.job_id)
    content = deepcopy(original)
    content["analysis_id"] = "an_" + suffix
    if rows is not None:
        content["preview_transactions"] = rows
        content["report_transactions"] = deepcopy(rows)
    batches.canonical_completion = completion
    record = batches.mark_completed(job.job_id, result=ConversionJobResultReference(
        analysis_id=content["analysis_id"], payload={"analysis": content, "organization_source": source or {}}))
    return record, content


def adopt(database, record=None, content=None):
    repo, scope, original, _, batches, _ = database
    if record:
        scope = replace(scope, job_id=record.job.job_id, analysis_id=content["analysis_id"])
    result = repo.ingest(scope, content or original, currency="BRL", product="pro", new_analysis=True,
                         rollout=CanonicalRollout(True, frozenset({scope.organization_id})), resolve_accounts=True)
    enable(database)
    with repo._connect() as conn:
        conn.execute(f'UPDATE {repo.p}canonical_analyses SET ui_enabled=TRUE')
    return result, scope


def query(database, **filters):
    from app.application.financial_center import FinancialCenterService

    repo, scope, *_ = database
    return FinancialCenterService(repo).month(scope.actor_id, scope.client_id, month=date(2026, 4, 1), **filters)


def test_calendar_reads_current_revision_decimal_and_source(database):
    repo, scope, *_ = database
    initial, _ = adopt(database)
    repo.apply(scope, [{"movement_id": initial["movement_ids"]["stable"], "date": "2026-04-02",
                       "description": "Recebimento corrigido", "debit": 2.75}],
               expected_version=1, operation_id="central", channel="pro")
    result = query(database)
    assert result["movements"][0]["description"] == "Recebimento corrigido"
    assert result["movements"][0]["amount"] == "-2.75"
    assert result["movements"][0]["date"] == "2026-04-02"
    assert result["movements"][0]["source"]["analysis_id"] == scope.analysis_id
    assert result["days"][1]["outflows"] == "2.75"
    assert result["days"][0]["coverage"] == "unknown"
    assert result["financial_reconciliation"] == "not_implemented"


def test_month_day_pagination_and_legitimate_repetitions(database):
    repo, _, original, _, batches, _ = database
    rows = [dict(original["preview_transactions"][0], transaction_id=f"row-{i}",
                 date="2026-04-02" if i < 3 else "2026-05-01") for i in range(4)]
    record, content = add_document(database, suffix="paging", rows=rows)
    adopt(database, record, content)
    first = query(database, day=2, limit=2)
    assert first["days"][1]["count"] == 3
    assert len(first["movements"]) == 2 and first["next_cursor"]
    second = query(database, day=2, limit=2, cursor=first["next_cursor"])
    assert len(second["movements"]) == 1 and second["next_cursor"] is None
    assert query(database, day=3)["movements"] == []


def test_independent_expiry_and_cross_client_denied(database):
    from app.application.errors import AnalysisNotFoundError
    from app.application.financial_center import FinancialCenterService

    repo, scope, *_ = database
    adopt(database)
    record, content = add_document(database, suffix="second")
    adopt(database, record, content)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_batches SET expires_at=clock_timestamp()-interval '1 second' "
                     f"WHERE batch_id=(SELECT batch_id FROM {repo.p}conversion_jobs WHERE job_id=%s)", (scope.job_id,))
    result = query(database)
    assert len(result["movements"]) == 1
    assert result["movements"][0]["source"]["analysis_id"] == content["analysis_id"]
    other = repo.test_access.register_user(name="Other", email="other@example.test", password="strong-password")
    repo.test_access.activate_user_plan(user_id=other.user_id, plan_code="essencial")
    with pytest.raises(AnalysisNotFoundError):
        FinancialCenterService(repo).month(other.user_id, scope.client_id, month=date(2026, 4, 1))


def test_uncertain_cross_document_duplicates_are_explicit_not_merged(database):
    adopt(database)
    record, content = add_document(database, suffix="overlap")
    adopt(database, record, content)
    result = query(database)
    assert len(result["movements"]) == 2
    assert all(row["possible_duplicate"] for row in result["movements"])
    assert result["days"][0]["inflows"] is None
    assert result["days"][0]["consolidation"] == "review_required"


def test_completion_identifies_client_before_adoption_and_resolves_row_account(database, tmp_path):
    import json

    from app.application.canonical_pro import CanonicalCompletion
    from app.application.storage_service import TempAnalysisStorage

    repo, scope, original, *_ = database
    storage = TempAnalysisStorage(tmp_path / "sources")
    source = {"holder_name": "Cliente automático", "holder_tax_id": "12345678000190", "confidence": "strong",
              "bank_code": "341", "branch": "1234", "account_number": "56789"}
    content = deepcopy(original)
    content["analysis_id"] = "an_automatic"
    path = storage.root_dir / content["analysis_id"] / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps(content), encoding="utf-8")
    with repo._connect() as conn:
        conn.execute(f'''INSERT INTO {repo.p}canonical_pro_cohorts
            (organization_id,ingestion_enabled,read_enabled,edit_enabled,ui_enabled) VALUES (%s,TRUE,TRUE,TRUE,TRUE)''',
                     (scope.organization_id,))
    record, _ = add_document(database, suffix="automatic", context=False, source=source,
                             completion=CanonicalCompletion(repo, storage))
    assert record.result.payload["authority"] == "postgres_v1"
    with repo._connect() as conn:
        movement = conn.execute(f'SELECT client_id, account_id FROM {repo.p}bank_movements').fetchone()
        assert movement["client_id"] and movement["account_id"]


def test_identical_reimport_reuses_review_and_original_retention(database, tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from app.application.canonical_pro import CanonicalCompletion
    from app.application.storage_service import TempAnalysisStorage

    repo, scope, *_ = database
    adopt(database)
    repo.apply(scope, [{"row_id": "stable", "date": "2026-04-01", "description": "Correção preservada", "credit": 12.50}],
               expected_version=1, operation_id="before-reimport", channel="pro")
    with repo._connect() as conn:
        conn.execute(f'''INSERT INTO {repo.p}canonical_pro_cohorts
            (organization_id,ingestion_enabled,read_enabled,edit_enabled,ui_enabled) VALUES (%s,TRUE,TRUE,TRUE,TRUE)''',
                     (scope.organization_id,))
    storage = TempAnalysisStorage(tmp_path/"duplicate")
    path = storage.root_dir / "an_identical" / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps({**database[2], "analysis_id": "an_identical"}), encoding="utf-8")
    record, _ = add_document(database, suffix="identical", digest="a"*64,
                             completion=CanonicalCompletion(repo, storage))
    assert record.result.analysis_id == scope.analysis_id
    assert record.result.payload["analysis"]["preview_transactions"][0]["description"] == "Correção preservada"
    assert record.expires_at <= datetime.fromisoformat(database[2]["expires_at"])
    with pytest.raises(Exception, match="SQL command adapter"):
        database[4].update_analysis(record.job.job_id, analysis=database[2])
    other_client = database[5].save_client(scope.actor_id, name="Other client", tax_id="98765432100")
    with pytest.raises(Exception, match="scope reassignment"):
        database[5].save_context(scope.actor_id, job_id=record.job.job_id, client_id=other_client["id"],
                                 account_id=None, period_ids=[], expected_version=1)
    rollback = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "20261102_02"],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "DATABASE_URL": DSN, "DATABASE_SCHEMA": database[3]},
        capture_output=True, text=True)
    assert rollback.returncode != 0 and "Retain canonical alias protection" in rollback.stderr
    with repo._connect() as conn:
        assert conn.execute(f'SELECT count(*) AS n FROM {repo.p}bank_movements').fetchone()["n"] == 1
        conn.execute(f"UPDATE {repo.p}conversion_batches SET expires_at=clock_timestamp()-interval '1 second' "
                     f"WHERE batch_id=(SELECT batch_id FROM {repo.p}conversion_jobs WHERE job_id=%s)", (scope.job_id,))
    assert query(database)["movements"] == []
    from app.application.canonical_pro import CanonicalProService
    from app.routers import canonical_pro
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: CanonicalProService(repo, None))
    unavailable = canonical_pro.canonical_display_record(record, scope.actor_id)
    assert unavailable.status.value == "expired" and unavailable.result is None


@pytest.fixture
def multiconta_document(database):
    from app.application.received_statements import ReceivedStatements

    repo, scope, original, _, batches, organization = database
    source = {"holder_name": "Client", "holder_tax_id": "12345678901", "confidence": "strong", "accounts": [
        {"bank_code": "341", "branch": "1234", "account_number": "111"},
        {"bank_code": "077", "branch": "0001", "account_number": "222"}]}
    rows = [dict(original["preview_transactions"][0], transaction_id=f"account-{i}", amount=value,
                 statement_account={"block_id": f"block-{i}", "account_number": number})
            for i, (number, value) in enumerate([("111", 10), ("222", -2.75), ("unknown", 3)])]
    record, content = add_document(database, suffix="multiconta", context=False, source=source, rows=rows)
    ReceivedStatements(repo.test_access.db, batches).receive(record)
    return record, content


def test_multiconta_evidence_filters_and_unknown_account(database, multiconta_document):
    repo, scope, original, _, batches, organization = database
    adopt(database, *multiconta_document)
    all_rows = query(database)["movements"]
    assert len(all_rows) == 3
    assert sum(row["account_id"] is None for row in all_rows) == 1
    accounts = organization.catalog(scope.actor_id)["accounts"]
    assert len(accounts) == 2
    for account in accounts:
        scoped = query(database, accounts=[account["id"]])
        assert len(scoped["movements"]) == 1
        assert scoped["movements"][0]["account_id"] == account["id"]
    assert len(query(database, accounts=["unidentified"])["movements"]) == 1


def test_multiconta_account_cannot_change_client_via_service_or_http(database, multiconta_document):
    import httpx

    from app.application.financial_center import FinancialCenterService
    from app.dependencies import get_access_control_service
    from app.main import app
    from app.routers.financial_center import financial_service
    from tests.test_convert_edits_http_real_api import _run_http_server

    repo, scope, _, _, _, organization = database
    record, content = multiconta_document
    adopt(database, record, content)
    assert organization.contexts(scope.actor_id, [record.job.job_id])[record.job.job_id]["account_id"] is None
    other = organization.save_client(scope.actor_id, name="Outro cliente")
    accounts = organization.catalog(scope.actor_id)["accounts"]
    assert len(accounts) == 2
    for account in accounts:
        before = query(database, accounts=[account["id"]])["movements"]
        with pytest.raises(ValueError, match="Mantenha o cliente"):
            organization.save_account(scope.actor_id, entity_id=account["id"], client_id=other["id"], name=account["name"])
        assert query(database, accounts=[account["id"]])["movements"] == before

    account = accounts[0]
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[financial_service] = lambda: FinancialCenterService(repo)
    with _run_http_server(configure) as base, httpx.Client(base_url=base, timeout=30, headers={
        "Authorization": f"Bearer {repo.test_user.token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web",
    }) as client:
        path = f'/api/pro/organization/accounts/{account["id"]}'
        denied = client.patch(path, json={"client_id": other["id"], "name": account["name"]})
        assert denied.status_code == 400 and "Mantenha o cliente" in denied.json()["detail"]
        renamed = client.patch(path, json={"client_id": scope.client_id, "name": "Nome corrigido"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "Nome corrigido"
        filtered = client.get(f'/api/pro/financial-center/clients/{scope.client_id}',
                              params={"month": "2026-04", "account": account["id"]})
        assert filtered.status_code == 200 and len(filtered.json()["movements"]) == 1

    # Retained history still owns its account after a logical deletion or expiry.
    with repo._connect() as conn:
        conn.execute(f'UPDATE {repo.p}bank_movements SET is_deleted=TRUE WHERE account_id=%s', (account["id"],))
        conn.execute(f"UPDATE {repo.p}canonical_analyses SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(ValueError, match="Mantenha o cliente"):
        organization.save_account(scope.actor_id, entity_id=account["id"], client_id=other["id"], name=account["name"])


def test_account_reassigned_before_adoption_cannot_cross_client(database, multiconta_document):
    from app.application.errors import AnalysisNotFoundError

    repo, scope, _, _, _, organization = database
    account = organization.catalog(scope.actor_id)["accounts"][0]
    other = organization.save_client(scope.actor_id, name="Outro cliente")
    changed = organization.save_account(scope.actor_id, entity_id=account["id"], client_id=other["id"], name=account["name"])
    assert changed["client_id"] == other["id"]
    # The existing ingestion guard rejects stale source links atomically.
    with pytest.raises(AnalysisNotFoundError):
        adopt(database, *multiconta_document)
    with repo._connect() as conn:
        assert conn.execute(f'SELECT count(*) AS n FROM {repo.p}bank_movements').fetchone()["n"] == 0
        assert conn.execute(f'SELECT count(*) AS n FROM {repo.p}canonical_analyses').fetchone()["n"] == 0


def test_account_reassignment_waits_for_adoption_then_rechecks(database, multiconta_document):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event
    from time import monotonic, sleep

    from app.application.pro_organization import OrganizationService

    repo, scope, _, _, _, organization = database
    record, content = multiconta_document
    account = organization.catalog(scope.actor_id)["accounts"][0]
    other = organization.save_client(scope.actor_id, name="Outro cliente")
    ready, backend_pid = Event(), []

    class ConcurrentDb:
        def __getattr__(self, name):
            return getattr(organization.db, name)

        @contextmanager
        def connect(self):
            with organization.db.connect() as conn:
                backend_pid.append(conn.info.backend_pid)
                ready.set()
                yield conn

    concurrent = OrganizationService(ConcurrentDb())
    # Retain the real ingestion transaction while a different connection attempts
    # reassignment, then commit and verify it sees the newly committed references.
    with ThreadPoolExecutor(max_workers=1) as executor:
        with repo._connect() as conn:
            repo.ingest(replace(scope, job_id=record.job.job_id, analysis_id=content["analysis_id"]), content,
                        currency="BRL", product="pro", new_analysis=True, connection=conn,
                        rollout=CanonicalRollout(True, frozenset({scope.organization_id})), resolve_accounts=True)
            moving = executor.submit(concurrent.save_account, scope.actor_id, entity_id=account["id"],
                                     client_id=other["id"], name=account["name"])
            assert ready.wait(10)
            deadline = monotonic() + 10
            with repo._connect() as observer:
                while monotonic() < deadline:
                    blocked = observer.execute("SELECT cardinality(pg_blocking_pids(%s)) AS n", (backend_pid[0],)).fetchone()["n"]
                    if blocked:
                        break
                    assert not moving.done(), "Reassignment must wait for adoption"
                    sleep(0.02)
                assert blocked, "Reassignment did not acquire its database lock"
        with pytest.raises(ValueError, match="Mantenha o cliente"):
            moving.result(timeout=10)
    assert next(a for a in organization.catalog(scope.actor_id)["accounts"] if a["id"] == account["id"])["client_id"] == scope.client_id


def test_documented_no_activity_is_distinct_from_unknown_coverage(database):
    from app.application.received_statements import ReceivedStatements

    repo, scope, original, _, batches, _ = database
    source = {"holder_name": "Client", "holder_tax_id": "12345678901", "confidence": "strong",
              "bank_code": "341", "branch": "1234", "account_number": "111"}
    record, content = add_document(database, suffix="empty", context=False, source=source, rows=[])
    content["pdf_processing_metrics"]["statement_no_activity"] = 1
    content.update(transactions_total=0, total_inflows=0, total_outflows=0, net_total=0, closing_balance=100)
    content["document_parts"] = [{"included_in_statement": True, "period": {
        "start_date": "2026-04-01", "end_date": "2026-04-15", "basis": "document_metadata"}}]
    batches.update_analysis(record.job.job_id, analysis=content)
    ReceivedStatements(repo.test_access.db, batches).receive(record)
    adopt(database, record, content)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (scope.job_id,))
    days = query(database)["days"]
    assert days[0]["coverage"] == "no_activity_confirmed"
    assert days[14]["coverage"] == "no_activity_confirmed"
    assert days[15]["coverage"] == "unknown"
    repo.apply(replace(scope, analysis_id=content["analysis_id"], job_id=record.job.job_id),
               [{"action": "insert", "date": "2026-04-01", "description": "Incluído na revisão", "credit": 5}],
               expected_version=1, operation_id="no-activity-correction", channel="pro")
    assert query(database)["days"][1]["coverage"] == "unknown"


def test_suspended_sources_legacy_and_mixed_currency_are_honest(database):
    repo, scope, *_ = database
    adopt(database)
    record, content = add_document(database, suffix="legacy")
    assert query(database)["legacy_sources"] == 1
    with repo._connect() as conn:
        conn.execute(f'UPDATE {repo.p}canonical_analyses SET ui_enabled=FALSE')
    result = query(database)
    assert result["unavailable_sources"] == 1 and not result["movements"]
    with repo._connect() as conn:
        conn.execute(f'UPDATE {repo.p}canonical_analyses SET ui_enabled=TRUE')
    other_scope = replace(scope, analysis_id=content["analysis_id"], job_id=record.job.job_id)
    repo.ingest(other_scope, content, currency="USD", product="pro", new_analysis=True,
                rollout=CanonicalRollout(True, frozenset({scope.organization_id})))
    enable(database)
    with repo._connect() as conn:
        conn.execute(f'UPDATE {repo.p}canonical_analyses SET ui_enabled=TRUE')
    assert query(database)["days"][0]["inflows"] is None


def test_http_financial_center_real_auth_failure_and_revision(database, monkeypatch):
    from time import perf_counter

    import httpx

    from app.application.financial_center import FinancialCenterService
    from app.dependencies import get_access_control_service
    from app.main import app
    from app.routers.financial_center import financial_service
    from tests.test_convert_edits_http_real_api import _run_http_server

    repo, scope, *_ = database
    adopt(database)
    service = FinancialCenterService(repo)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[financial_service] = lambda: service
    with _run_http_server(configure) as base, httpx.Client(base_url=base, timeout=30) as client:
        path = f"/api/pro/financial-center/clients/{scope.client_id}?month=2026-04"
        assert client.get(path).status_code == 401
        client.headers["Authorization"] = f"Bearer {repo.test_user.token}"
        start = perf_counter()
        response = client.get(path)
        print(f"financial_center_http_ms={(perf_counter()-start)*1000:.1f}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["movements"][0]["amount"] == "10.00"
        assert client.get(path + "&day=31").status_code == 400
        assert client.get(path + "&account=foreign-account").status_code == 404
        repo.apply(scope, [{"row_id": "stable", "date": "2026-04-01", "description": "Atualizado", "credit": 11}],
                   expected_version=1, operation_id="http-central", channel="pro")
        assert client.get(path).json()["movements"][0]["description"] == "Atualizado"
        def fail(*args, **kwargs):
            raise ConnectionError("private database details")
        monkeypatch.setattr(service, "month", fail)
        failed = client.get(path)
        assert failed.status_code == 503 and "private database" not in failed.text


def test_two_tenants_and_other_client_rows_never_enter_totals(database):
    from app.application.financial_center import FinancialCenterService

    repo, scope, original, _, batches, organization = database
    adopt(database)
    for owner, suffix in [(repo.test_user, "own"),
                          (repo.test_access.register_user(name="Other tenant", email="tenant@example.test", password="strong-password"),
                           "foreign")]:
        repo.test_access.activate_user_plan(user_id=owner.user_id, plan_code="essencial")
        other_client = organization.save_client(owner.user_id, name="Other client", tax_id="98765432100")
        identity = repo.test_access.resolve_identity(anonymous_fingerprint=None, user_token=owner.token)
        batch = ConversionBatch.create(identity_type="user", identity_id=owner.user_id, files_count=1, idempotency_key=suffix)
        job = ConversionJob.create(batch_id=batch.batch_id, identity=identity, idempotency_key=suffix,
                                   document=batches.get(scope.job_id).job.document)
        batches.create(batch=batch, jobs=[job])
        organization.save_context(owner.user_id, job_id=job.job_id, client_id=other_client["id"], account_id=None,
                                  period_ids=[], expected_version=0)
        for transition in (batches.mark_uploaded, batches.mark_queued, batches.mark_running):
            transition(job.job_id)
        content = deepcopy(original)
        content["analysis_id"] = "an_"+suffix
        for key in ("preview_transactions", "report_transactions"):
            content[key][0]["amount"] = 999
        batches.mark_completed(job.job_id, result=ConversionJobResultReference(
            analysis_id=content["analysis_id"], payload={"analysis": content}))
        with repo._connect() as conn:
            org = conn.execute(f'SELECT id FROM {repo.p}pro_organizations WHERE owner_user_id=%s', (owner.user_id,)).fetchone()["id"]
        repo.ingest(MovementScope(owner.user_id, org, job.job_id, content["analysis_id"], other_client["id"]),
                    content, currency="BRL", product="pro", new_analysis=True,
                    rollout=CanonicalRollout(True, frozenset({org})))
        enable(database)
        with repo._connect() as conn:
            conn.execute(f'UPDATE {repo.p}canonical_analyses SET ui_enabled=TRUE')
        other = FinancialCenterService(repo).month(owner.user_id, other_client["id"], month=date(2026, 4, 1))
        assert other["days"][0]["inflows"] == "999.00"
    own = query(database)
    assert len(own["movements"]) == 1 and own["days"][0]["inflows"] == "10.00"
