"""add checkout intents table

Revision ID: 20260504_02
Revises: 20260504_01
Create Date: 2026-05-04 00:30:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260504_02"
down_revision: Union[str, Sequence[str], None] = "20260504_01"
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
        CREATE TABLE IF NOT EXISTS "{schema}".checkout_intents (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            plan_name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            billing_period TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_whatsapp TEXT NOT NULL,
            customer_document TEXT,
            customer_notes TEXT
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_checkout_intents_created_at
        ON "{schema}".checkout_intents(created_at)
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".checkout_intents')
