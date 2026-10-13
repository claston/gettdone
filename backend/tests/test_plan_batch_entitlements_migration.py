from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20261012_01_plan_batch_entitlements.py"
    spec = importlib.util.spec_from_file_location("plan_batch_entitlements_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_batch_entitlements_migration_backfills_existing_plans_and_defaults_new_plans_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    statements: list[str] = []
    monkeypatch.setenv("DATABASE_SCHEMA", "product")
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    combined = "\n".join(statements)
    assert 'ALTER TABLE "product".plan_versions' in combined
    assert "ADD COLUMN IF NOT EXISTS batch_conversion_enabled BOOLEAN" in combined
    assert "ADD COLUMN IF NOT EXISTS batch_max_files INTEGER" in combined
    assert "SET batch_conversion_enabled = TRUE" in combined
    assert "batch_max_files = 12" in combined
    assert "ALTER COLUMN batch_conversion_enabled SET DEFAULT FALSE" in combined
    assert "ALTER COLUMN batch_max_files SET DEFAULT 1" in combined
    assert "CHECK (batch_max_files BETWEEN 1 AND 12)" in combined
    assert "DROP TABLE" not in combined
    assert "RENAME" not in combined

    statements.clear()
    migration.downgrade()

    downgrade_sql = "\n".join(statements)
    assert "DROP CONSTRAINT IF EXISTS ck_plan_versions_batch_max_files" in downgrade_sql
    assert "DROP COLUMN IF EXISTS batch_max_files" in downgrade_sql
    assert "DROP COLUMN IF EXISTS batch_conversion_enabled" in downgrade_sql


def test_plan_batch_entitlements_migration_rejects_unsafe_schema_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')

    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        migration.upgrade()
