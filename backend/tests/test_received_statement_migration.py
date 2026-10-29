import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.application.organization_schema_v1 import organization_schema_v1
from app.application.received_statement_schema_v1 import TABLES, received_statement_schema_v1


def test_additive_receipt_schema_repeated_upgrade_and_reverse_cleanup_preserve_legacy():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES ('owner')")
        for statement in organization_schema_v1():
            conn.execute(statement)
        conn.execute("INSERT INTO pro_organizations VALUES ('org', 'owner', 'now')")
        for _ in range(2):
            for statement in received_statement_schema_v1():
                conn.execute(statement)
        conn.execute("INSERT INTO pro_clients VALUES ('c', 'org', 'Legacy', NULL, '', 'owner', 'now', 'now')")
        conn.execute("INSERT INTO pro_received_statements VALUES ('org', 'j', 'b', 'f.pdf', 'now', 'later', 'ready', '{}')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pro_received_statement_accounts VALUES ('org', 'j', 'foreign')")
        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE {table}")
        assert conn.execute("SELECT name FROM pro_clients").fetchall() == [("Legacy",)]
        for statement in received_statement_schema_v1():
            conn.execute(statement)
        assert conn.execute("SELECT COUNT(*) FROM pro_received_statements").fetchone()[0] == 0


def test_receipt_migration_chain_and_schema_validation(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261028_02_received_statements.py"
    spec = importlib.util.spec_from_file_location("received_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "20261028_01"
    statements = []
    monkeypatch.setenv("DATABASE_SCHEMA", "receipt_test")
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert sum("CREATE TABLE" in sql for sql in statements) == 2
    assert not any("ALTER" in sql or "DROP" in sql for sql in statements)
    module.downgrade()
    assert statements[-2:] == [f'DROP TABLE IF EXISTS "receipt_test".{table}' for table in reversed(TABLES)]
    monkeypatch.setenv("DATABASE_SCHEMA", 'bad"schema')
    with pytest.raises(ValueError):
        module.upgrade()
