"""Real PostgreSQL upgrade/runtime/rollback, against a disposable local CI database only."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application import AccessControlService
from app.application.pro_organization import OrganizationService


@pytest.mark.skipif(not os.getenv("PRO_ORGANIZATION_TEST_DATABASE_URL"), reason="Requires disposable local PostgreSQL")
def test_postgres_upgrade_runtime_rollback_and_reupgrade(tmp_path):
    import psycopg
    from psycopg import sql

    from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository
    from app.application.conversion.contracts.documents import ConversionDocumentReference
    from app.application.conversion.conversion_batch import ConversionBatch
    from app.application.conversion.conversion_job import ConversionJob

    dsn = os.environ["PRO_ORGANIZATION_TEST_DATABASE_URL"]
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path == "/ofx24_test"
    schema = f"ofx24_test_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": dsn, "DATABASE_SCHEMA": schema}

    def migrate(target):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade" if target == "head" else "downgrade", target],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    access = None
    try:
        migrate("head")
        access = AccessControlService(
            state_file=tmp_path / "access.json", token_secret="postgres-organization-test", database_url=dsn, database_schema=schema
        )
        user = access.register_user(name="Owner", email="owner@example.test", password="strong-password")
        service = OrganizationService(access.db)
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
        repository = PostgresConversionBatchRepository(database_url=dsn, database_schema=schema)
        batch = ConversionBatch.create(identity_type="user", identity_id=user.user_id, files_count=1, idempotency_key="pg-source")
        job = ConversionJob.create(
            batch_id=batch.batch_id,
            identity=identity,
            idempotency_key="pg-job",
            document=ConversionDocumentReference(
                storage_key="doc_" + "a" * 24,
                filename="metadata-only.csv",
                file_type="csv",
                size_bytes=1,
                sha256_hex="a" * 64,
            ),
        )
        repository.create(batch=batch, jobs=[job])
        summaries = repository.list_files_for_owner(identity_type="user", identity_id=user.user_id, limit=2)
        assert len(summaries) == 1 and summaries[0].filename == "metadata-only.csv"
        assert repository.list_files_for_owner(identity_type="user", identity_id="other-owner", limit=2) == ()
        client = service.save_client(user.user_id, name="Cliente real", tax_id="12345678901")
        period = service.save_period(user.user_id, name="Intervalo", start_date=date(2026, 9, 20), end_date=date(2026, 10, 4))
        context = service.save_context(
            user.user_id, job_id=job.job_id, client_id=client["id"], account_id=None, period_ids=[period["id"]], expected_version=0
        )
        assert context["version"] == 1
        assert service.catalog(user.user_id)["periods"][0]["start_date"] == "2026-09-20"
        assert service.contexts(user.user_id, [job.job_id])[job.job_id]["client_id"] == client["id"]
        from app.application.conversion.contracts.jobs import ConversionJobResultReference
        from app.application.received_statements import ReceivedStatements
        from app.application.statement_works import StatementWorkService

        receipts = ReceivedStatements(access.db, repository)
        receipts.receive(repository.get(job.job_id))
        receipt_page = receipts.dashboard(user.user_id)
        assert receipt_page["clients"][0]["id"] == client["id"]
        assert receipt_page["clients"][0]["historical_count"] == 1
        assert receipts.documents(user.user_id, client["id"])[0]["batch_id"] == batch.batch_id
        works = StatementWorkService(access.db, repository)
        account = service.save_account(user.user_id, client_id=client["id"], name="Principal")
        work = works.create(user.user_id, client["id"], period["id"])
        assert works.create(user.user_id, client["id"], period["id"])["id"] == work["id"]
        work = works.scope(user.user_id, work["id"], work["version"], [account["id"]])
        work = works.attach(user.user_id, work["id"], work["version"], job.job_id, account["id"], 1)
        repository.mark_uploaded(job.job_id)
        repository.mark_queued(job.job_id)
        repository.mark_running(job.job_id)
        repository.mark_completed(
            job.job_id,
            result=ConversionJobResultReference(
                analysis_id="an_pg_work",
                payload={
                    "analysis": {
                        "preview_transactions": [{"transaction_id": "tx1", "date": "2026-10-01", "description": "PIX", "amount": 12}]
                    },
                    "reviewed_at": "2026-10-27T12:00:00Z",
                },
            ),
        )
        work = works.annotate(
            user.user_id,
            work["id"],
            work["version"],
            "evidence",
            job.job_id,
            {"identity_confirmed": True, "start_date": "2026-09-20", "end_date": "2026-10-04"},
        )
        assert work["assessment"]["can_complete"]
        work = works.complete(user.user_id, work["id"], work["version"], work["fingerprint"], {})
        assert not work["needs_reassessment"]
        assert len(works.list(user.user_id)["items"]) == 1
        work = works.reopen(user.user_id, work["id"], work["version"], "Conferência adicional")
        work = works.complete(user.user_id, work["id"], work["version"], work["fingerprint"], {})
        assert len(work["closings"]) == 2
        access.close()
        access = None
        migrate("20261024_01")
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert conn.execute(sql.SQL("SELECT count(*) FROM {}.pro_clients").format(sql.Identifier(schema))).fetchone()[0] == 1
            assert conn.execute(sql.SQL("SELECT count(*) FROM {}.conversion_jobs").format(sql.Identifier(schema))).fetchone()[0] == 1
            assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.pro_statement_works",)).fetchone()[0] is None
        migrate("head")
        access = AccessControlService(
            state_file=tmp_path / "works-after.json", token_secret="postgres-organization-test", database_url=dsn, database_schema=schema
        )
        assert StatementWorkService(access.db, repository).list(user.user_id)["items"] == []
        assert OrganizationService(access.db).catalog(user.user_id)["clients"][0]["id"] == client["id"]
        access.close()
        access = None
        migrate("20261012_01")
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert conn.execute(sql.SQL("SELECT count(*) FROM {}.users").format(sql.Identifier(schema))).fetchone()[0] == 1
            assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.pro_clients",)).fetchone()[0] is None
        migrate("head")
        access = AccessControlService(
            state_file=tmp_path / "after.json", token_secret="postgres-organization-test", database_url=dsn, database_schema=schema
        )
        assert OrganizationService(access.db).catalog(user.user_id) == {"clients": [], "accounts": [], "periods": []}
    finally:
        if access is not None:
            access.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
