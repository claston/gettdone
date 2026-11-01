import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.application.client_identity_schema_v1 import client_identity_schema_v1
from app.application.organization_schema_v1 import organization_schema_v1


def test_confirmation_upgrade_old_writes_rollback_and_reupgrade(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261101_01_client_identity_confirmation.py"
    spec = importlib.util.spec_from_file_location("client_identity_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "20261031_01"
    monkeypatch.setenv("DATABASE_SCHEMA", "public")
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES ('owner')")
        for sql in organization_schema_v1():
            conn.execute(sql)
        conn.execute("INSERT INTO pro_organizations VALUES ('org', 'owner', 'now')")
        conn.execute("INSERT INTO pro_clients VALUES ('c', 'org', 'Legacy', NULL, '', 'owner', 'now', 'now')")
        monkeypatch.setattr(module.op, "execute", lambda sql: conn.execute(sql.replace('"public".', "")))
        module.upgrade()
        module.upgrade()
        assert conn.execute("SELECT client_id FROM pro_client_identity_confirmations").fetchall() == [("c",)]
        # Production's previous version can still write its unchanged entities.
        conn.execute("UPDATE pro_clients SET name = 'Corrected' WHERE id = 'c'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pro_client_identity_confirmations VALUES ('org', 'foreign', 'now')")
        module.downgrade()
        assert conn.execute("SELECT name FROM pro_clients").fetchall() == [("Corrected",)]
        module.upgrade()
        assert conn.execute("SELECT COUNT(*) FROM pro_client_identity_confirmations").fetchone()[0] == 1
    with pytest.raises(ValueError):
        client_identity_schema_v1('bad"schema')
