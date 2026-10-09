from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261009_01_pdf_native_geometry_shadow.py"
    )
    spec = importlib.util.spec_from_file_location("pdf_native_geometry_shadow_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pdf_native_geometry_shadow_migration_is_additive_and_indexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    statements: list[str] = []
    monkeypatch.setenv("DATABASE_SCHEMA", "product")
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert 'CREATE TABLE IF NOT EXISTS "product".pdf_native_geometry_shadow_events' in sql
    assert "PRIMARY KEY (processing_id, identity_type)" in sql
    assert "geometry_duration_ms" in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "DROP " not in sql

    statements.clear()
    migration.downgrade()

    assert statements == ['DROP TABLE IF EXISTS "product".pdf_native_geometry_shadow_events']


def test_pdf_native_geometry_shadow_migration_rejects_unsafe_schema_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')

    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        migration.upgrade()
