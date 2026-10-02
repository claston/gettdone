from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261001_01_product_telemetry.py"
    )
    spec = importlib.util.spec_from_file_location("product_telemetry_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_telemetry_migration_is_additive_and_indexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    statements: list[str] = []
    monkeypatch.setenv("DATABASE_SCHEMA", "product")
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert 'CREATE TABLE IF NOT EXISTS "product".product_events' in sql
    assert "event_type" in sql
    assert "identity_type" in sql
    assert "download_format" in sql
    assert "processing_id" in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "DROP " not in sql

    statements.clear()
    migration.downgrade()

    assert statements == ['DROP TABLE IF EXISTS "product".product_events']


def test_product_telemetry_migration_rejects_unsafe_schema_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')

    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        migration.upgrade()
