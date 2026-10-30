import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261029_01_conversion_feedback.py"
    spec = importlib.util.spec_from_file_location("conversion_feedback_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_additive_validates_schema_and_preserves_answers_on_downgrade(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setenv("DATABASE_SCHEMA", "feedback_test")
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert 'CREATE TABLE IF NOT EXISTS "feedback_test".conversion_feedback' in statements[0]
    assert "CREATE INDEX IF NOT EXISTS" in statements[1]
    assert "DROP " not in "\n".join(statements)
    statements.clear()
    module.downgrade()
    assert statements == []
    monkeypatch.setenv("DATABASE_SCHEMA", 'unsafe"schema')
    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        module.upgrade()


@pytest.mark.skipif(not os.getenv("ADMIN_METRICS_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_upgrade_retry_old_writer_and_rollback_keep_feedback(monkeypatch):
    import psycopg

    url = os.environ["ADMIN_METRICS_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in ("localhost", "127.0.0.1")
    schema = f"feedback_{uuid4().hex}"
    module = migration()
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    with psycopg.connect(url, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'CREATE TABLE "{schema}".user_conversions (analysis_id TEXT PRIMARY KEY, status TEXT)')
            monkeypatch.setattr(module.op, "execute", conn.execute)
            module.upgrade()
            module.upgrade()
            conn.execute(f'INSERT INTO "{schema}".user_conversions VALUES (%s, %s)', ("old-writer", "Sucesso"))
            conn.execute(f'''INSERT INTO "{schema}".conversion_feedback
                (processing_id, identity_type, identity_id, offered_at, answer, answered_at)
                VALUES ('old-writer', 'registered', 'person', '2026-10-29T15:00:00+00:00', 'yes', '2026-10-29T15:01:00+00:00')''')
            module.downgrade()
            assert conn.execute(f'SELECT status FROM "{schema}".user_conversions').fetchone() == ("Sucesso",)
            assert conn.execute(f'SELECT answer FROM "{schema}".conversion_feedback').fetchone() == ("yes",)
            module.upgrade()
            assert conn.execute(f'SELECT COUNT(*) FROM "{schema}".conversion_feedback').fetchone() == (1,)
        finally:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.mark.skipif(not os.getenv("ADMIN_METRICS_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_full_migration_chain_and_feedback_service(tmp_path):
    import psycopg

    from app.application.access_control import AccessControlService
    from app.application.admin_dashboard_service import AdminDashboardService
    from app.application.conversion_feedback import save_feedback

    url = os.environ["ADMIN_METRICS_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in ("localhost", "127.0.0.1")
    schema = f"feedback_app_{uuid4().hex}"
    service = None
    try:
        process = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}, capture_output=True, text=True, timeout=120,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test",
                                       database_url=url, database_schema=schema)
        identity = service.resolve_identity(anonymous_fingerprint="browser", user_token=None)
        save_feedback(service, identity=identity, processing_id="conversion", answer=None)
        save_feedback(service, identity=identity, processing_id="conversion", answer="no")
        save_feedback(service, identity=identity, processing_id="conversion", answer="yes")
        assert save_feedback(service, identity=identity, processing_id="conversion", answer=None)["answer"] == "yes"
        result = AdminDashboardService(service).get_dashboard()["conversion_feedback"]
        assert (result["offered_count"], result["yes_count"], result["no_count"], result["unanswered_count"]) == (1, 1, 0, 0)
    finally:
        if service:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
