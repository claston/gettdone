"""add conversion telemetry observability columns

Revision ID: 20260531_01
Revises: 20260527_01
Create Date: 2026-05-31 00:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20260531_01"
down_revision: Union[str, Sequence[str], None] = "20260527_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema() -> str:
    raw = (os.getenv("DATABASE_SCHEMA", "public") or "").strip()
    if not raw:
        return "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
    return raw


def _add_observability_columns(table: str, schema: str) -> None:
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS error_stage TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS error_subcode TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS exception_class TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS layout_inference_name TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS layout_inference_confidence DOUBLE PRECISION')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS selected_parser TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS parser_selection_reason TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS pdf_page_count INTEGER')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS extracted_char_count INTEGER')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS ocr_attempted BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS ocr_engine TEXT')
    op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS file_sha256 TEXT')


def _drop_observability_columns(table: str, schema: str) -> None:
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS file_sha256')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS ocr_engine')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS ocr_attempted')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS extracted_char_count')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS pdf_page_count')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS parser_selection_reason')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS selected_parser')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS layout_inference_confidence')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS layout_inference_name')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS exception_class')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS error_subcode')
    op.execute(f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS error_stage')


def upgrade() -> None:
    schema = _schema()
    _add_observability_columns("user_conversions", schema)
    _add_observability_columns("anonymous_conversion_events", schema)


def downgrade() -> None:
    schema = _schema()
    _drop_observability_columns("anonymous_conversion_events", schema)
    _drop_observability_columns("user_conversions", schema)
