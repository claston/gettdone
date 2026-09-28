from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260927_01_version_marketing_consent.py"
    )
    spec = importlib.util.spec_from_file_location("version_marketing_consent_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_marketing_consent_migration_is_additive_and_backfills_legacy_opt_ins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    statements: list[str] = []
    monkeypatch.setenv("DATABASE_SCHEMA", "product")
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert statements == [
        'ALTER TABLE "product".users ADD COLUMN IF NOT EXISTS product_updates_consent_version INTEGER',
        (
            'UPDATE "product".users SET product_updates_consent_version = 1 '
            "WHERE product_updates_opt_in = TRUE AND product_updates_consent_version IS NULL"
        ),
    ]
    assert "DROP " not in statements[0]
    assert statements[1].startswith('UPDATE "product".users')

    statements.clear()
    migration.downgrade()

    assert statements == [
        'ALTER TABLE "product".users DROP COLUMN IF EXISTS product_updates_consent_version'
    ]


def test_marketing_consent_migration_rejects_unsafe_schema_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv("DATABASE_SCHEMA", 'public"; DROP TABLE users; --')

    with pytest.raises(RuntimeError, match="valid PostgreSQL schema"):
        migration.upgrade()
