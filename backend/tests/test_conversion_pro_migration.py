"""Real PostgreSQL additive upgrade and code-rollback safety in a private schema."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.getenv("TEST_PRO_CONVERSION_PG_DSN"), reason="isolated local PostgreSQL DSN not configured")
def test_postgres_pro_upgrade_keeps_old_writers_and_retained_rollback(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from threading import Barrier

    import psycopg

    from app.adapters.conversion.access import PostgresConversionAccessService
    from app.application.access_control import AccessControlService
    from app.application.conversion.identity import IdentityContext
    from app.application.conversion.pro_conversion import ConversionProQuota, ConversionProQuotaError, ProConversionPolicy

    dsn = os.environ["TEST_PRO_CONVERSION_PG_DSN"]
    if "@127.0.0.1:" not in dsn:
        pytest.fail("This migration check only permits an isolated loopback PostgreSQL instance")
    schema = "test_conversion_pro_" + uuid4().hex
    path = Path(__file__).parents[1] / "alembic/versions/20261031_01_conversion_pro.py"
    spec = importlib.util.spec_from_file_location("migration_pro_validation", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'CREATE TABLE "{schema}".conversion_jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL)')
            conn.execute(f"INSERT INTO \"{schema}\".conversion_jobs VALUES ('old-job','completed')")
            monkeypatch.setenv("DATABASE_SCHEMA", schema)
            monkeypatch.setattr(migration.op, "execute", conn.execute)
            migration.upgrade()
            migration.upgrade()
            assert conn.execute(f"SELECT ocr_mode FROM \"{schema}\".conversion_jobs WHERE job_id='old-job'").fetchone()[0] == "standard"
            conn.execute(f"INSERT INTO \"{schema}\".conversion_jobs (job_id,status) VALUES ('old-writer','queued')")
            conn.execute(f"INSERT INTO \"{schema}\".conversion_jobs VALUES ('pro-job','completed','pro')")
            conn.execute(f"INSERT INTO \"{schema}\".conversion_pro_usage VALUES ('pro-job','owner','2026-10',4,4,'finished','2026-10-31')")
            migration.downgrade()
            assert conn.execute(f'SELECT attempted_pages FROM "{schema}".conversion_pro_usage').fetchone()[0] == 4
            conn.execute(f"INSERT INTO \"{schema}\".conversion_jobs (job_id,status) VALUES ('after-code-rollback','queued')")
            assert conn.execute(f'SELECT COUNT(*) FROM "{schema}".conversion_jobs').fetchone()[0] == 4

            # Two independent workers must serialize through PostgreSQL, not a shared Python lock.
            monkeypatch.setattr(AccessControlService, "_init_db", lambda self: None)
            services = [
                AccessControlService(
                    tmp_path / "api.json",
                    "test-only",
                    database_url=dsn,
                    database_schema=schema,
                    now_provider=lambda: datetime(2026, 10, 31, tzinfo=timezone.utc),
                ),
                PostgresConversionAccessService(
                    database_url=dsn,
                    database_schema=schema,
                    now_provider=lambda: datetime(2026, 10, 31, tzinfo=timezone.utc),
                ),
            ]
            try:
                for service in services:
                    monkeypatch.setattr(service, "_read_active_user_plan", lambda **kwargs: {"code": "profissional"})
                identity = IdentityContext(
                    "user", "concurrent-owner", 300, quota_mode="pages", plan_code="profissional", max_pages_per_file_ocr=6
                )
                budgets = [ConversionProQuota(service, ProConversionPolicy(True, 6, 5)) for service in services]
                barrier = Barrier(2)

                def reserve(index):
                    barrier.wait(timeout=10)
                    try:
                        budgets[index].reserve(f"concurrent-{index}", identity, 4)
                        return index
                    except ConversionProQuotaError:
                        return None

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(reserve, range(2)))
                winners = [index for index in results if index is not None]
                assert len(winners) == 1
                winner = winners[0]
                budgets[winner].before_page(f"concurrent-{winner}", identity, 1)
                budgets[winner].finish(f"concurrent-{winner}", identity)
                assert budgets[1 - winner].remaining(identity) == 4
            finally:
                for service in services:
                    service.close()
        finally:
            assert schema.startswith("test_conversion_pro_")
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
