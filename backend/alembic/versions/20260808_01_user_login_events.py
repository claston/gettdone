"""add user login events

Revision ID: 20260808_01
Revises: 20260610_02
Create Date: 2026-08-08 00:10:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260808_01"
down_revision: Union[str, Sequence[str], None] = "20260610_02"
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
        CREATE TABLE IF NOT EXISTS "{schema}".user_login_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES "{schema}".users(id),
            auth_method TEXT NOT NULL CHECK (auth_method IN ('local_password', 'google_oauth')),
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_login_events_user_created_at
        ON "{schema}".user_login_events(user_id, created_at DESC)
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".user_login_events')
