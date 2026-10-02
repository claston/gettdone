"""add product telemetry events

Revision ID: 20261001_01
Revises: 20260927_01
Create Date: 2026-10-01 12:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20261001_01"
down_revision: Union[str, Sequence[str], None] = "20260927_01"
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
        CREATE TABLE IF NOT EXISTS "{schema}".product_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            identity_type TEXT NOT NULL CHECK (identity_type IN ('registered', 'anonymous')),
            identity_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            page_path TEXT,
            plan_code TEXT,
            processing_id TEXT,
            download_format TEXT
        )
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_product_events_created_at '
        f'ON "{schema}".product_events(created_at)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_product_events_identity_created_at '
        f'ON "{schema}".product_events(identity_type, identity_id, created_at)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_product_events_type_created_at '
        f'ON "{schema}".product_events(event_type, created_at)'
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".product_events')
