"""add native PDF geometry shadow telemetry

Revision ID: 20261009_01
Revises: 20261001_01
Create Date: 2026-10-09 12:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20261009_01"
down_revision: Union[str, Sequence[str], None] = "20261001_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema() -> str:
    raw = (os.getenv("DATABASE_SCHEMA", "public") or "").strip()
    if not raw:
        return "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
    return raw


def upgrade() -> None:
    schema = _schema()
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".pdf_native_geometry_shadow_events (
            processing_id TEXT NOT NULL,
            identity_type TEXT NOT NULL CHECK (identity_type IN ('registered', 'anonymous')),
            created_at TEXT NOT NULL,
            classification TEXT NOT NULL,
            baseline_status TEXT NOT NULL,
            baseline_layout TEXT,
            baseline_parser TEXT,
            baseline_transactions INTEGER NOT NULL DEFAULT 0,
            baseline_balance_failed INTEGER NOT NULL DEFAULT 0,
            geometry_status TEXT NOT NULL,
            geometry_layout TEXT,
            geometry_parser TEXT,
            geometry_transactions INTEGER NOT NULL DEFAULT 0,
            geometry_balance_failed INTEGER NOT NULL DEFAULT 0,
            geometry_duration_ms INTEGER NOT NULL DEFAULT 0,
            matched_transactions INTEGER NOT NULL DEFAULT 0,
            date_conflicts INTEGER NOT NULL DEFAULT 0,
            amount_conflicts INTEGER NOT NULL DEFAULT 0,
            sign_conflicts INTEGER NOT NULL DEFAULT 0,
            geometry_error_type TEXT,
            word_count INTEGER NOT NULL DEFAULT 0,
            line_count INTEGER NOT NULL DEFAULT 0,
            duplicate_characters_removed INTEGER NOT NULL DEFAULT 0,
            fragment_merges INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (processing_id, identity_type)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pdf_native_geometry_shadow_created_at "
        f'ON "{schema}".pdf_native_geometry_shadow_events(created_at)'
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pdf_native_geometry_shadow_classification "
        f'ON "{schema}".pdf_native_geometry_shadow_events(classification, created_at)'
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".pdf_native_geometry_shadow_events')
