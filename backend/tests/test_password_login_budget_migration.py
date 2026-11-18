import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application import AccessControlService
from app.application.login_rate_limit import LoginRateLimitedError, enforce_password_login_budget


def test_shadow_and_login_migrations_have_one_deployable_head():
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    head = scripts.get_current_head()
    assert head is not None
    for deployed in ("20261117_01", "20261118_01"):
        assert list(scripts.iterate_revisions(head, deployed))


@pytest.mark.skipif(not os.getenv("AUTH_RATE_LIMIT_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
@pytest.mark.parametrize("deployed", ["20261117_01", "20261118_01"])
def test_upgrade_from_either_reviewed_head_and_rollback_preserve_data(deployed):
    import psycopg

    url = os.environ["AUTH_RATE_LIMIT_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    schema = "test_review_merge_" + uuid4().hex[:12]
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}
    backend = Path(__file__).resolve().parents[1]
    run_id = str(uuid4())

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

    def seed_shadow(conn):
        conn.execute(f'''INSERT INTO "{schema}".classification_shadow_runs
            (id, organization_id, client_id, actor_id, source_kind, source_id, source_version,
             engine_version, input_hash, request, state, expires_at)
            VALUES (%s, 'org', 'client', 'actor', 'movement', %s, 1, 'test', 'evidence', '{{}}', 'ready', now() + interval '1 day')''',
                     (run_id, str(uuid4())))
        conn.execute(f'''INSERT INTO "{schema}".classification_shadow_feedback(run_id, item_id, verdict, actor_id)
            VALUES (%s, 'movement', 'agree', 'actor')''', (run_id,))

    def seed_budget(conn):
        conn.execute(f'''INSERT INTO "{schema}".password_login_budgets(budget_key, window_started_at, attempts)
            VALUES ('synthetic-budget', 12345, 10)''')

    try:
        migrate("upgrade", deployed)
        with psycopg.connect(url) as conn:
            (seed_shadow if deployed == "20261117_01" else seed_budget)(conn)
            conn.execute(f'''INSERT INTO "{schema}".users(id, name, email, password_hash, password_salt, created_at, updated_at)
                VALUES ('usr_old', 'Old writer', 'old@example.test', 'hash', 'salt', '2026-11-18', '2026-11-18')''')
        migrate("upgrade", "head")
        with psycopg.connect(url) as conn:
            (seed_budget if deployed == "20261117_01" else seed_shadow)(conn)
        for direction, revision in (("downgrade", "20261116_01"), ("upgrade", "head")):
            migrate(direction, revision)
            with psycopg.connect(url) as conn:
                assert str(conn.execute(f'SELECT id FROM "{schema}".classification_shadow_runs').fetchone()[0]) == run_id
                assert conn.execute(f'SELECT verdict FROM "{schema}".classification_shadow_feedback').fetchone() == ("agree",)
                assert conn.execute(f'SELECT attempts FROM "{schema}".password_login_budgets').fetchone() == (10,)
                conn.execute(f'UPDATE "{schema}".users SET name=\'Compatible writer\' WHERE id=\'usr_old\'')
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def exercise_concurrent_budget(services):
    def attempt(index):
        try:
            enforce_password_login_budget(services[index % len(services)], email="target@example.test", ip_address=f"192.0.2.{index}")
            return True
        except LoginRateLimitedError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(20))) == 10


def test_sqlite_budget_is_atomic_between_instances_and_does_not_store_identifiers(tmp_path):
    settings = dict(state_file=tmp_path / "state.json", token_secret="test",
                    now_provider=lambda: datetime(2026, 11, 18, 12, tzinfo=timezone.utc))
    services = [AccessControlService(**settings) for _ in range(2)]
    try:
        exercise_concurrent_budget(services)
        with services[0]._connect() as conn:
            keys = [row["budget_key"] for row in conn.execute("SELECT budget_key FROM password_login_budgets")]
        assert keys and all(len(key) == 64 and "@" not in key and "." not in key for key in keys)
    finally:
        for service in services:
            service.close()


@pytest.mark.skipif(not os.getenv("AUTH_RATE_LIMIT_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_upgrade_concurrency_old_writer_and_rollback_preserve_budgets(tmp_path):
    import psycopg

    url = os.environ["AUTH_RATE_LIMIT_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    schema = "test_login_budget_" + uuid4().hex[:12]
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}
    backend = Path(__file__).resolve().parents[1]
    services = []

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

    try:
        migrate("upgrade", "20261116_01")
        with psycopg.connect(url) as conn:
            conn.execute(f'''INSERT INTO "{schema}".users(id, name, email, password_hash, password_salt, created_at, updated_at)
                VALUES ('usr_old', 'Old writer', 'old@example.test', 'hash', 'salt', '2026-11-18', '2026-11-18')''')
        migrate("upgrade", "head")
        settings = dict(state_file=tmp_path / "state.json", token_secret="test", database_url=url, database_schema=schema,
                        now_provider=lambda: datetime(2026, 11, 18, 12, tzinfo=timezone.utc))
        services = [AccessControlService(**settings) for _ in range(2)]
        exercise_concurrent_budget(services)
        migrate("downgrade", "20261116_01")
        with psycopg.connect(url) as conn:
            assert conn.execute(f'SELECT password_hash FROM "{schema}".users WHERE id=\'usr_old\'').fetchone() == ("hash",)
            conn.execute(f'UPDATE "{schema}".users SET name=\'Old writer after rollback\' WHERE id=\'usr_old\'')
            assert conn.execute(f'SELECT count(*) FROM "{schema}".password_login_budgets').fetchone()[0] == 21
        migrate("upgrade", "head")
        with pytest.raises(LoginRateLimitedError):
            enforce_password_login_budget(services[0], email="target@example.test", ip_address="192.0.2.99")
    finally:
        for service in services:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
