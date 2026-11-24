"""Real PostgreSQL upgrade/re-upgrade, old readers, quotas and expiration."""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application.access_control import AccessControlService
from app.application.canonical_pro import CanonicalProService
from app.application.errors import AnalysisAccessDeniedError
from tests.test_black_friday import OFFERS, START


@pytest.mark.skipif(not os.getenv("BLACK_FRIDAY_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_upgrade_rollback_preserves_contracts(tmp_path):
    import psycopg

    url = os.environ["BLACK_FRIDAY_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    schema = "black_" + uuid4().hex
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}
    service = None

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend,
                                env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr

    try:
        migrate("upgrade", "20261122_01")
        with psycopg.connect(url) as conn:
            original = conn.execute(f'SELECT * FROM "{schema}".plan_versions ORDER BY code').fetchall()
        for direction, revision in (("upgrade", "head"), ("downgrade", "20261122_01"), ("upgrade", "head")):
            migrate(direction, revision)
            with psycopg.connect(url) as conn:
                assert conn.execute(f'SELECT * FROM "{schema}".plan_versions WHERE is_public ORDER BY code').fetchall() == original
                assert conn.execute(f'SELECT count(*) FROM "{schema}".plan_versions WHERE NOT is_public').fetchone()[0] == 3
        clock = [START]
        service = AccessControlService(state_file=tmp_path / "access.json", token_secret="black-test",
                                       database_url=url, database_schema=schema, now_provider=lambda: clock[0])
        assert {item["code"] for item in service.list_public_plans()} == set(OFFERS)
        user = service.register_user(name="Cliente", email="black@example.test", password="safe-password")
        service.activate_user_plan(user_id=user.user_id, plan_code="black_friday_3_meses")
        identity = service.resolve_identity(None, user.token)
        service.consume_quota(identity, consumed_units=1000)
        clock[0] = datetime(2026, 12, 25, 3, tzinfo=timezone.utc)
        assert service.get_remaining_quota(identity) == 1000
        # Verify both canonical PRO access and expiration against real SQL.
        pro = CanonicalProService.__new__(CanonicalProService)
        pro.repository = SimpleNamespace(p=f'"{schema}".')
        with psycopg.connect(url) as conn:
            conn.execute(f'UPDATE "{schema}".user_plan_subscriptions SET ended_at = (CURRENT_TIMESTAMP + INTERVAL \'1 day\')::text')
            pro.assert_entitled(conn, user.user_id)
            conn.execute(f'UPDATE "{schema}".user_plan_subscriptions SET ended_at = \'2020-01-01T00:00:00+00:00\'')
            with pytest.raises(AnalysisAccessDeniedError):
                pro.assert_entitled(conn, user.user_id)
        service._invalidate_active_plan_cache(user.user_id)
        assert service.resolve_identity(None, user.token).plan_code is None
        migrate("downgrade", "20261122_01")
        migrate("upgrade", "head")
        with psycopg.connect(url) as conn:
            assert conn.execute(f'SELECT count(*) FROM "{schema}".user_plan_subscriptions').fetchone()[0] == 1
    finally:
        if service:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
