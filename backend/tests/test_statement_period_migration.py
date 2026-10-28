"""The additive migration preserves old writers and retained period evidence."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261028_01_statement_period_history.py"
    spec = importlib.util.spec_from_file_location("statement_period_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_statement_period_migration_is_additive_idempotent_and_retains_data_on_rollback(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setenv("DATABASE_SCHEMA", "metrics_fixture")
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert len(statements) == 6
    assert all('ALTER TABLE "metrics_fixture".' in statement and "ADD COLUMN IF NOT EXISTS" in statement
               and statement.endswith(" TEXT") for statement in statements)
    statements.clear()
    module.downgrade()
    assert not statements
    monkeypatch.setenv("DATABASE_SCHEMA", 'unsafe"schema')
    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        module.upgrade()


@pytest.mark.skipif(not os.getenv("ADMIN_METRICS_TEST_DATABASE_URL"), reason="Requires an isolated local PostgreSQL database")
def test_statement_period_migration_forward_old_writers_rollback_and_retry_on_postgres(monkeypatch):
    import psycopg

    module = migration()
    assert urlparse(os.environ["ADMIN_METRICS_TEST_DATABASE_URL"]).hostname in ("127.0.0.1", "localhost")
    schema = f"admin_metrics_{uuid4().hex}"
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    with psycopg.connect(os.environ["ADMIN_METRICS_TEST_DATABASE_URL"], autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            for table in ("user_conversions", "anonymous_conversion_events"):
                conn.execute(f'CREATE TABLE "{schema}".{table} (id TEXT PRIMARY KEY, file_sha256 TEXT, status TEXT)')
                conn.execute(f'INSERT INTO "{schema}".{table} VALUES (%s, %s, %s)', ("old", "a" * 64, "Falha"))
            monkeypatch.setattr(module.op, "execute", conn.execute)
            module.upgrade()
            module.upgrade()
            for table in ("user_conversions", "anonymous_conversion_events"):
                row = conn.execute(f'SELECT file_sha256, statement_period_start FROM "{schema}".{table} WHERE id=\'old\'').fetchone()
                assert row == ("a" * 64, None)
                conn.execute(f'INSERT INTO "{schema}".{table} (id, status) VALUES (\'old-writer\', \'Sucesso\')')
                conn.execute(f'UPDATE "{schema}".{table} SET statement_period_start=\'2026-09-01\', '
                             "statement_period_end='2026-09-30', statement_period_basis='document_metadata' WHERE id='old'")
            module.downgrade()
            module.upgrade()
            for table in ("user_conversions", "anonymous_conversion_events"):
                row = conn.execute(f'SELECT statement_period_start, statement_period_end, statement_period_basis '
                                   f'FROM "{schema}".{table} WHERE id=\'old\'').fetchone()
                assert row == ("2026-09-01", "2026-09-30", "document_metadata")
                assert conn.execute(f'SELECT count(*) FROM "{schema}".{table}').fetchone()[0] == 2
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.skipif(not os.getenv("ADMIN_METRICS_TEST_DATABASE_URL"), reason="Requires an isolated local PostgreSQL database")
def test_postgres_history_and_admin_metrics_after_alembic_upgrade(tmp_path):
    import psycopg

    from app.application.access_control import AccessControlService
    from app.application.admin_dashboard_service import AdminDashboardService

    dsn = os.environ["ADMIN_METRICS_TEST_DATABASE_URL"]
    assert urlparse(dsn).hostname in ("127.0.0.1", "localhost")
    schema = f"admin_metrics_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": dsn, "DATABASE_SCHEMA": schema}
    access = None
    try:
        process = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend,
                                 env=env, capture_output=True, text=True, timeout=120)
        assert process.returncode == 0, process.stdout + process.stderr
        access = AccessControlService(state_file=tmp_path / "access.json", token_secret="fixture-secret",
                                      database_url=dsn, database_schema=schema)
        user = access.register_user(name="Fixture", email="metrics@example.test", password="test-password")
        period = {"start_date": "2026-09-01", "end_date": "2026-09-30", "basis": "document_metadata"}
        for identifier in ("attempt-1", "attempt-2"):
            access.record_user_conversion(user_id=user.user_id, processing_id=identifier, filename="private.pdf",
                                          model="Banco", conversion_type="pdf-ofx", status="Falha", transactions_count=0,
                                          file_sha256="f" * 64, statement_period=period, document_type="bank_statement")
        result = AdminDashboardService(access).get_dashboard(days=1)["pdf_documents"]
        assert result["failure_attempts"] == 2 and result["unique_failed_documents"] == 1
        assert result["by_period"][0]["label"] == "09/2026"
        assert result["attention"][0]["statement_period"] == period
    finally:
        if access:
            access.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
