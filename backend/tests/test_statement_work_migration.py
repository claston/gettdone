import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.application.organization_schema_v1 import organization_schema_v1
from app.application.statement_work_schema_v1 import TABLES, statement_work_schema_v1


def test_additive_upgrade_downgrade_reupgrade_preserve_existing_data():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE users(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES ('owner')")
        for statement in organization_schema_v1():
            conn.execute(statement)
        conn.execute("INSERT INTO pro_organizations VALUES ('o','owner','now')")
        conn.execute("INSERT INTO pro_clients VALUES ('c','o','Cliente',NULL,'','owner','now','now')")
        conn.execute("INSERT INTO pro_periods VALUES ('p','o','Período','2026-09-20','2026-10-19','owner','now','now')")
        for _ in range(2):
            for statement in statement_work_schema_v1():
                conn.execute(statement)
        conn.execute("INSERT INTO pro_statement_works VALUES ('w','o','c','p','open',1,'{}','now','now','owner','owner')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pro_statement_works VALUES ('duplicate','o','c','p','open',1,'{}','now','now','owner','owner')")
        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE {table}")
        assert conn.execute("SELECT name FROM pro_clients").fetchone()[0] == "Cliente"
        assert conn.execute("SELECT id FROM users").fetchone()[0] == "owner"
        for statement in statement_work_schema_v1():
            conn.execute(statement)
        assert conn.execute("SELECT count(*) FROM pro_statement_works").fetchone()[0] == 0


def test_migration_validates_schema_before_upgrade_or_downgrade(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261027_01_statement_works.py"
    spec = importlib.util.spec_from_file_location("work_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    monkeypatch.setenv("DATABASE_SCHEMA", "test_work")
    migration.upgrade()
    assert len([s for s in statements if "CREATE TABLE" in s]) == 3
    assert all("ALTER TABLE" not in s and "DROP" not in s for s in statements)
    statements.clear()
    migration.downgrade()
    assert statements == [f'DROP TABLE IF EXISTS "test_work".{table}' for table in reversed(TABLES)]
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')
    with pytest.raises(ValueError):
        migration.upgrade()
    with pytest.raises(ValueError):
        migration.downgrade()
