from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261010_01_document_type_observability.py"
    )
    spec = importlib.util.spec_from_file_location("document_type_observability_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_document_type_observability_migration_is_additive_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    statements: list[str] = []
    monkeypatch.setenv("DATABASE_SCHEMA", "product")
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert len(statements) == 10
    assert statements[0] == (
        'ALTER TABLE "product".user_conversions '
        'ADD COLUMN IF NOT EXISTS document_type TEXT'
    )
    assert statements[-1] == (
        'ALTER TABLE "product".anonymous_conversion_events '
        'ADD COLUMN IF NOT EXISTS document_classification_evidence_json TEXT'
    )
    assert all("DROP " not in statement and "RENAME " not in statement for statement in statements)

    statements.clear()
    migration.downgrade()

    assert len(statements) == 10
    assert all("DROP COLUMN IF EXISTS" in statement for statement in statements)


def test_document_type_observability_migration_rejects_unsafe_schema_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')

    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        migration.upgrade()
