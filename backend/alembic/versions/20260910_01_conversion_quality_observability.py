"""add conversion quality observability

Revision ID: 20260910_01
Revises: 20260829_01
Create Date: 2026-09-10 12:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20260910_01"
down_revision: Union[str, Sequence[str], None] = "20260829_01"
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
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS quality_status TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS quality_rule_version TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS quality_reason_codes_json TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS parser_confidence_band TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS parser_coverage_rate DOUBLE PRECISION')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS warning_types_json TEXT')
        op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS failure_diagnostics_json TEXT')

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".conversion_quality_issues (
            conversion_id TEXT NOT NULL,
            identity_type TEXT NOT NULL CHECK (identity_type IN ('registered', 'anonymous')),
            issue_index INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            scope TEXT NOT NULL,
            severity TEXT NOT NULL,
            issue_code TEXT NOT NULL,
            transaction_index INTEGER,
            source_page INTEGER,
            source_line INTEGER,
            source_parser TEXT,
            details_json TEXT,
            PRIMARY KEY (conversion_id, identity_type, issue_index)
        )
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_conversion_quality_issues_created_at '
        f'ON "{schema}".conversion_quality_issues(created_at)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_conversion_quality_issues_issue_code '
        f'ON "{schema}".conversion_quality_issues(issue_code)'
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".conversion_quality_issues')
    for table in ("user_conversions", "anonymous_conversion_events"):
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS failure_diagnostics_json')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS warning_types_json')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS parser_coverage_rate')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS parser_confidence_band')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS quality_reason_codes_json')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS quality_rule_version')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS quality_score')
        op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS quality_status')
