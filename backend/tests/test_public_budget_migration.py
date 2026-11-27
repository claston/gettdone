import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application import AccessControlService
from app.application.public_request_budget import PublicBudgetExceeded, enforce_public_budget


@pytest.mark.skipif(not os.getenv("AUTH_RATE_LIMIT_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_upgrade_rollback_revocation_and_concurrent_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    import psycopg

    url = os.environ["AUTH_RATE_LIMIT_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    schema = "test_public_budget_" + uuid4().hex[:12]
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}
    services = []

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

    try:
        migrate("upgrade", "20261124_01")
        with psycopg.connect(url) as conn:
            conn.execute(f'''INSERT INTO "{schema}".users(id, name, email, password_hash, password_salt, created_at, updated_at)
                VALUES ('usr_old', 'Old writer', 'old@example.test', 'hash', 'salt', '2026-11-25', '2026-11-25')''')
        migrate("upgrade", "head")
        settings = dict(state_file=tmp_path / "state.json", token_secret="synthetic", database_url=url,
                        database_schema=schema, anonymous_quota_limit=3,
                        now_provider=lambda: datetime(2026, 11, 25, 12, tzinfo=timezone.utc))
        services = [AccessControlService(**settings) for _ in range(2)]
        bundle = services[0].create_user_session(user_id="usr_old")

        def attempt(index):
            try:
                enforce_public_budget(services[index % 2], kind="conversion", ip_address="192.0.2.1")
                return True
            except PublicBudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(attempt, range(12))) == 3

        spec = importlib.util.spec_from_file_location("revoke_legacy_tokens", backend / "scripts/revoke_legacy_tokens.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with psycopg.connect(url) as conn:
            assert script.revoke_legacy_tokens(conn, schema=schema)["changed_users"] == 0
            assert conn.execute(f'SELECT credential_version FROM "{schema}".users').fetchone() == (0,)
            assert script.revoke_legacy_tokens(conn, schema=schema, apply=True)["changed_users"] == 1
            assert script.revoke_legacy_tokens(conn, schema=schema, apply=True)["changed_users"] == 0
        assert services[0].get_user_by_session_access_token(bundle.access_token).user_id == "usr_old"
        for direction, revision in (("downgrade", "20261124_01"), ("upgrade", "head")):
            migrate(direction, revision)
            with psycopg.connect(url) as conn:
                assert conn.execute(f'SELECT credential_version FROM "{schema}".users').fetchone() == (script.REVOCATION_VERSION,)
                conn.execute(f'UPDATE "{schema}".users SET name=\'Old writer after rollback\' WHERE id=\'usr_old\'')
            with pytest.raises(PublicBudgetExceeded):
                enforce_public_budget(services[0], kind="conversion", ip_address="192.0.2.1")
    finally:
        for service in services:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
