import asyncio
import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application.marketing_campaign_schema_v1 import marketing_campaign_schema_v1
from app.application.marketing_campaign_service import MarketingCampaignService
from tests.test_marketing_email_api import FakeResend


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261115_01_manual_marketing_campaign.py"
    spec = importlib.util.spec_from_file_location("marketing_campaign_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_additive_schema_retains_ledger_on_rollback_and_reupgrade(monkeypatch):
    module = migration()
    with sqlite3.connect(":memory:") as conn:
        conn.execute("ATTACH DATABASE ':memory:' AS campaign_test")
        # Execute the shared portable DDL against a real SQL engine.
        for sql in marketing_campaign_schema_v1():
            conn.execute(sql)
        conn.execute("INSERT INTO marketing_campaigns "
                     "(id,subject,text_template,html_template,from_email,public_base_url,created_at,created_by) "
                     "VALUES ('guide','s','t','h','f','u','now','admin')")
        conn.execute("INSERT INTO marketing_campaign_deliveries (campaign_id,user_id,email,consent_at,status,updated_at) "
                     "VALUES ('guide','u','user@example.com','then','sent','now')")
        monkeypatch.setattr(module.op, "execute", lambda sql: conn.execute(sql.replace('"campaign_test".', '')))
        monkeypatch.setenv("DATABASE_SCHEMA", "campaign_test")
        module.upgrade()
        module.downgrade()
        module.upgrade()
        assert conn.execute("SELECT status FROM marketing_campaign_deliveries").fetchone()[0] == "sent"
        monkeypatch.setenv("DATABASE_SCHEMA", 'unsafe"schema')
        with pytest.raises(ValueError, match="Invalid database schema"):
            module.upgrade()


@pytest.mark.skipif(not os.getenv("ADMIN_METRICS_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_full_upgrade_old_writer_campaign_and_rollback(monkeypatch, tmp_path):
    import psycopg

    from app.application.access_control import AccessControlService

    url = os.environ["ADMIN_METRICS_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in ("localhost", "127.0.0.1")
    schema = f"marketing_{uuid4().hex}"
    module = migration()
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    monkeypatch.setenv("MARKETING_SENDER_COMPANY", "Synthetic sender")
    monkeypatch.setenv("MARKETING_SENDER_CNPJ", "00.000.000/0000-00")
    monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", "https://www.ofxsimples.com.br")
    service = None
    try:
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120,
                                env={**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema})
        assert result.returncode == 0, result.stdout + result.stderr
        service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test", database_url=url, database_schema=schema)
        user = service.register_user(name="Teste", email="recipient@example.com", password="strong-pass",
                                     product_updates_opt_in=True, product_updates_consent_version=2,
                                     product_updates_opted_in_at="2026-11-15T00:00:00+00:00")
        campaign = MarketingCampaignService(service, user.user_id)
        mailer = FakeResend()
        mailer.require_live_sender = lambda: "novidades@example.com"
        assert campaign.prepare(mailer=mailer, expected_eligible=1)["total"] == 1
        assert asyncio.run(campaign.send_next(mailer=mailer))["sent"] == 1
        with psycopg.connect(url, autocommit=True) as conn:
            monkeypatch.setattr(module.op, "execute", conn.execute)
            module.upgrade()
            module.downgrade()
            module.upgrade()
        assert asyncio.run(campaign.send_next(mailer=mailer))["sent"] == 1
        assert len(mailer.messages) == 1
        # Existing account writes continue after the additive migration and retained rollback.
        service.register_user(name="Other", email="other@example.com", password="strong-pass")
    finally:
        if service:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
