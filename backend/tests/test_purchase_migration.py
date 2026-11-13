import importlib.util
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application import AccessControlService, InvalidUserTokenError
from app.application.purchase_flow import PurchaseFlow


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261112_01_purchase_setup.py"
    spec = importlib.util.spec_from_file_location("purchase_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_purchase_migration_is_additive_and_rejects_unsafe_identifiers(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setenv("DATABASE_SCHEMA", "purchase_test")
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert len(statements) == 6
    assert all("IF NOT EXISTS" in statement and "DROP " not in statement for statement in statements)
    statements.clear()
    module.downgrade()
    assert "initial_access_pending" in statements[0] and "RAISE EXCEPTION" in statements[0]
    assert "DROP " not in statements[0]
    monkeypatch.setenv("DATABASE_SCHEMA", 'bad"schema')
    with pytest.raises(ValueError):
        module.upgrade()


@pytest.mark.skipif(not os.getenv("PURCHASE_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_upgrade_rollback_and_concurrent_release(tmp_path, monkeypatch):
    import psycopg

    url = os.environ["PURCHASE_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
    schema = f"purchase_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    services = []
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}

    def migrate(direction, revision):
        return subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend,
                              env=env, capture_output=True, text=True, timeout=120)

    try:
        result = migrate("upgrade", "20261109_01")
        assert result.returncode == 0, result.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            insert = f'''INSERT INTO "{schema}".users
                (id, name, email, password_hash, password_salt, created_at, updated_at)
                VALUES (%s, 'Legacy', %s, 'preserved-hash', 'preserved-salt', '2026-11-12', '2026-11-12')'''
            conn.execute(insert, ("usr_old", "old@example.com"))
        for direction, version in (("upgrade", "head"), ("downgrade", "20261109_01"), ("upgrade", "head")):
            result = migrate(direction, version)
            assert result.returncode == 0, result.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(insert, ("usr_old_writer", "old-writer@example.com"))
            rows = conn.execute(f'SELECT initial_access_pending, password_hash FROM "{schema}".users').fetchall()
            assert rows == [(False, "preserved-hash"), (False, "preserved-hash")]
        for _ in range(2):
            services.append(AccessControlService(state_file=tmp_path / "state.json", token_secret="test",
                                                 database_url=url, database_schema=schema))
        intent = services[0].create_checkout_intent(user_id=None, plan_code="profissional", customer_name="Buyer",
                                                   customer_email="buyer@example.com", customer_whatsapp="11999999999")
        kwargs = dict(create_account=True, actor_kind="admin_user", actor_user_id=None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: PurchaseFlow(service).release(intent["id"], **kwargs), services))
        assert sorted(results) == [False, True]
        user_id = services[0].read_checkout_intent_by_id(intent_id=intent["id"])["user_id"]
        with pytest.raises(InvalidUserTokenError):
            services[0].get_user_by_token(services[0]._encode_token(user_id))
        result = migrate("downgrade", "20261109_01")
        assert result.returncode != 0 and "Pending first access accounts exist" in result.stderr
        token_id, token = PurchaseFlow(services[0]).issue_token(intent["id"], "setup")

        def confirm(service):
            try:
                PurchaseFlow(service).confirm(token=token, password="secure-password", accepted_terms=True)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(confirm, services)) == [False, True]
        assert services[0].authenticate_user("buyer@example.com", "secure-password").user_id == user_id
        with psycopg.connect(url) as conn:
            assert conn.execute(f'SELECT COUNT(*) FROM "{schema}".user_plan_subscriptions WHERE user_id = %s',
                                (user_id,)).fetchone()[0] == 1
            assert conn.execute(f'SELECT token_hash FROM "{schema}".purchase_tokens WHERE id = %s',
                                (token_id,)).fetchone()[0] != token
    finally:
        for service in services:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
