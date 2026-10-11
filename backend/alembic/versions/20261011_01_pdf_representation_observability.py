"""add PDF representation observability

Revision ID: 20261011_01
Revises: 20261010_01
Create Date: 2026-10-11 01:30:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "20261011_01"
down_revision: Union[str, Sequence[str], None] = "20261010_01"
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
    columns = (
        ("pdf_representation", "TEXT"),
        ("pdf_creation_method", "TEXT"),
        ("pdf_representation_confidence", "DOUBLE PRECISION"),
        ("pdf_classification_version", "TEXT"),
        ("pdf_classification_evidence_json", "TEXT"),
    )
    for table in ("user_conversions", "anonymous_conversion_events"):
        for column_name, column_type in columns:
            op.execute(
                f'ALTER TABLE "{schema}".{table} '
                f'ADD COLUMN IF NOT EXISTS {column_name} {column_type}'
            )


def downgrade() -> None:
    schema = _schema()
    columns = (
        "pdf_classification_evidence_json",
        "pdf_classification_version",
        "pdf_representation_confidence",
        "pdf_creation_method",
        "pdf_representation",
    )
    for table in ("user_conversions", "anonymous_conversion_events"):
        for column_name in columns:
            op.execute(
                f'ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS {column_name}'
            )
