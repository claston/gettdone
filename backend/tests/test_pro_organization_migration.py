import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.application.organization_schema_v1 import TABLES, organization_schema_v1


def test_additive_schema_constraints_and_reverse_cleanup_preserve_existing_users():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES ('owner')")
        for _ in range(2):
            for statement in organization_schema_v1():
                conn.execute(statement)
        conn.execute("INSERT INTO pro_organizations VALUES ('org', 'owner', 'now')")
        conn.execute("INSERT INTO pro_clients VALUES ('client', 'org', 'Name', NULL, '', 'owner', 'now', 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pro_accounts VALUES ('account', 'org', 'foreign', 'Name', '', '', '', 'owner', 'now', 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pro_periods VALUES ('p', 'org', 'Invalid', '2026-10-02', '2026-10-01', 'owner', 'now', 'now')")
        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE {table}")
        assert conn.execute("SELECT id FROM users").fetchall() == [("owner",)]


def test_migration_upgrade_downgrade_and_schema_validation(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261024_01_pro_organization.py"
    spec = importlib.util.spec_from_file_location("organization_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    monkeypatch.setenv("DATABASE_SCHEMA", "organization_test")
    module.upgrade()
    assert len([line for line in statements if "CREATE TABLE" in line]) == 6
    assert not any("ALTER TABLE" in line or "DROP" in line or "UPDATE users" in line for line in statements)
    assert all('"organization_test".' in line for line in statements)
    statements.clear()
    module.downgrade()
    assert statements == [f'DROP TABLE IF EXISTS "organization_test".{table}' for table in reversed(TABLES)]
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')
    with pytest.raises(ValueError, match="Invalid database schema"):
        module.upgrade()
    with pytest.raises(ValueError):
        module.downgrade()
