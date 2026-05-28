"""add conversion history metrics parity columns

Revision ID: 20260527_01
Revises: 20260526_01
Create Date: 2026-05-27 00:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260527_01"
down_revision: Union[str, Sequence[str], None] = "20260526_01"
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
    op.execute(f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS scanned_likely BOOLEAN')
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS ocr_used BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS ocr_pages_processed INTEGER NOT NULL DEFAULT 0'
    )
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS duration_ms INTEGER NOT NULL DEFAULT 0'
    )
    op.execute(f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS error_code TEXT')
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0'
    )
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS balance_consistency_failed INTEGER NOT NULL DEFAULT 0'
    )

    op.execute(
        f'ALTER TABLE "{schema}".anonymous_conversion_events '
        "ADD COLUMN IF NOT EXISTS canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        f'ALTER TABLE "{schema}".anonymous_conversion_events ADD COLUMN IF NOT EXISTS balance_consistency_failed INTEGER NOT NULL DEFAULT 0'
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f'ALTER TABLE "{schema}".anonymous_conversion_events DROP COLUMN IF EXISTS balance_consistency_failed'
    )
    op.execute(
        f'ALTER TABLE "{schema}".anonymous_conversion_events DROP COLUMN IF EXISTS canonical_warning_transactions_count'
    )
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS balance_consistency_failed')
    op.execute(
        f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS canonical_warning_transactions_count'
    )
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS error_code')
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS duration_ms')
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS ocr_pages_processed')
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS ocr_used')
    op.execute(f'ALTER TABLE "{schema}".user_conversions DROP COLUMN IF EXISTS scanned_likely')
