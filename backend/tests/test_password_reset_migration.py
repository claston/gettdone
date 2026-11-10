import importlib.util
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application import AccessControlService, InvalidCredentialsError, InvalidUserTokenError
from app.application.access_control.access_control_password_reset import InvalidPasswordResetTokenError


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261109_01_password_reset.py"
    spec = importlib.util.spec_from_file_location("password_reset_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_additive_and_blocks_unsafe_rollback(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setenv("DATABASE_SCHEMA", "test_recovery")
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert len(statements) == 3
    assert all("IF NOT EXISTS" in statement for statement in statements)
    assert not any("DROP " in statement for statement in statements)
    statements.clear()
    module.downgrade()
    assert len(statements) == 1
    assert 'LOCK TABLE "test_recovery".users IN SHARE MODE' in statements[0]
    assert 'credential_version > 0' in statements[0]
    assert 'RAISE EXCEPTION' in statements[0]
    assert 'DROP ' not in statements[0]
    monkeypatch.setenv("DATABASE_SCHEMA", 'unsafe"schema')
    with pytest.raises(ValueError, match="Invalid PostgreSQL schema"):
        module.upgrade()
    with pytest.raises(ValueError, match="Invalid PostgreSQL schema"):
        module.downgrade()


@pytest.mark.skipif(not os.getenv("PASSWORD_RESET_TEST_DATABASE_URL"), reason="Requires isolated local PostgreSQL")
def test_postgres_upgrade_existing_users_and_atomic_reset(tmp_path, monkeypatch):
    import psycopg

    url = os.environ["PASSWORD_RESET_TEST_DATABASE_URL"]
    assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
    schema = f"password_reset_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    services = []
    try:
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "20261103_01"], cwd=backend,
                                env={**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema},
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            # An old writer omits the new column, both before and after migration.
            insert = f'''INSERT INTO "{schema}".users
                (id, name, email, password_hash, password_salt, created_at, updated_at)
                VALUES (%s, 'Legacy', %s, 'existing-hash', 'existing-salt', '2026-11-09', '2026-11-09')'''
            conn.execute(insert, ("usr_old", "old@example.com"))
            module = migration()
            monkeypatch.setenv("DATABASE_SCHEMA", schema)
            monkeypatch.setattr(module.op, "execute", conn.execute)
            module.upgrade()
            module.upgrade()
            conn.execute(insert, ("usr_old_writer", "old-writer@example.com"))
            assert conn.execute(f'SELECT credential_version FROM "{schema}".users').fetchall() == [(0,), (0,)]
            module.downgrade()
            assert conn.execute(f'SELECT password_hash FROM "{schema}".users WHERE id=\'usr_old\'').fetchone() == ("existing-hash",)
        env = {**os.environ, "DATABASE_URL": url, "DATABASE_SCHEMA": schema}

        def migrate(direction, revision):
            return subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd=backend,
                                  env=env, capture_output=True, text=True, timeout=120)

        for direction, revision in (("upgrade", "head"), ("downgrade", "20261103_01"), ("upgrade", "head")):
            result = migrate(direction, revision)
            assert result.returncode == 0, result.stdout + result.stderr
        for _ in range(2):
            services.append(AccessControlService(state_file=tmp_path / "state.json", token_secret="test",
                                                 database_url=url, database_schema=schema))
        user = services[0].register_user(name="Recovery", email="recover@example.com", password="old-password")
        token = services[0].password_reset.issue(email=user.email)

        def confirm(service):
            try:
                service.password_reset.confirm(token=token.token, password="new-password")
                return True
            except InvalidPasswordResetTokenError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(confirm, services)) == [False, True]
        with pytest.raises(InvalidCredentialsError):
            services[0].authenticate_user(user.email, "old-password")
        with pytest.raises(InvalidUserTokenError):
            services[0].get_user_by_token(user.token)
        assert services[0].authenticate_user(user.email, "new-password").user_id == user.user_id
        result = migrate("downgrade", "20261103_01")
        assert result.returncode != 0
        assert "Retain password reset schema" in result.stderr
        with psycopg.connect(url) as conn:
            assert conn.execute(f'SELECT version_num FROM "{schema}".alembic_version').fetchone() == ("20261109_01",)
            assert conn.execute(f'SELECT credential_version FROM "{schema}".users WHERE id = %s', (user.user_id,)).fetchone() == (1,)
        with pytest.raises(InvalidUserTokenError):
            services[0].get_user_by_token(user.token)
    finally:
        for service in services:
            service.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
