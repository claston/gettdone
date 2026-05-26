"""add max_pages_per_file_ocr to plan_versions

Revision ID: 20260526_01
Revises: 20260520_01
Create Date: 2026-05-26 00:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260526_01"
down_revision: Union[str, Sequence[str], None] = "20260520_01"
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
        ALTER TABLE "{schema}".plan_versions
        ADD COLUMN IF NOT EXISTS max_pages_per_file_ocr INTEGER
        """
    )
    op.execute(
        f"""
        UPDATE "{schema}".plan_versions
        SET max_pages_per_file_ocr = 6
        WHERE max_pages_per_file_ocr IS NULL
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        ALTER COLUMN max_pages_per_file_ocr SET DEFAULT 6
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        ALTER COLUMN max_pages_per_file_ocr SET NOT NULL
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        DROP COLUMN IF EXISTS max_pages_per_file_ocr
        """
    )

