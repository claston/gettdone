"""add configurable batch conversion entitlements to plan versions

Revision ID: 20261012_01
Revises: 20261011_01
Create Date: 2026-10-12 00:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20261012_01"
down_revision: Union[str, Sequence[str], None] = "20261011_01"
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
        ADD COLUMN IF NOT EXISTS batch_conversion_enabled BOOLEAN
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        ADD COLUMN IF NOT EXISTS batch_max_files INTEGER
        """
    )
    # Every plan version that predates this capability is a paid plan. Preserve
    # its current commercial behavior while making future plans opt in.
    op.execute(
        f"""
        UPDATE "{schema}".plan_versions
        SET batch_conversion_enabled = TRUE,
            batch_max_files = 12
        WHERE batch_conversion_enabled IS NULL
           OR batch_max_files IS NULL
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        ALTER COLUMN batch_conversion_enabled SET DEFAULT FALSE,
        ALTER COLUMN batch_conversion_enabled SET NOT NULL,
        ALTER COLUMN batch_max_files SET DEFAULT 1,
        ALTER COLUMN batch_max_files SET NOT NULL
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        ADD CONSTRAINT ck_plan_versions_batch_max_files
        CHECK (batch_max_files BETWEEN 1 AND 12)
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        DROP CONSTRAINT IF EXISTS ck_plan_versions_batch_max_files
        """
    )
    op.execute(
        f"""
        ALTER TABLE "{schema}".plan_versions
        DROP COLUMN IF EXISTS batch_max_files,
        DROP COLUMN IF EXISTS batch_conversion_enabled
        """
    )
