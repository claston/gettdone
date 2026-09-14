"""add canonical layout capture observability

Revision ID: 20260914_01
Revises: 20260910_01
Create Date: 2026-09-14 12:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20260914_01"
down_revision: Union[str, Sequence[str], None] = "20260910_01"
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
    for table in ("user_conversions", "anonymous_conversion_events"):
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS canonical_capture_status TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS canonical_capture_reason TEXT')


def downgrade() -> None:
    schema = _schema()
    for table in ("user_conversions", "anonymous_conversion_events"):
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS canonical_capture_reason')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS canonical_capture_status')
