"""enforce one active plan subscription per user

Revision ID: 20260508_01
Revises: 20260504_02
Create Date: 2026-05-08 00:40:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260508_01"
down_revision: Union[str, Sequence[str], None] = "20260504_02"
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
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY started_at DESC, id DESC
                ) AS rn
            FROM "{schema}".user_plan_subscriptions
            WHERE status = 'active'
        )
        UPDATE "{schema}".user_plan_subscriptions ups
        SET
            status = 'ended',
            ended_at = COALESCE(ups.ended_at, NOW()::text)
        FROM ranked
        WHERE ups.id = ranked.id AND ranked.rn > 1
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_plan_subscriptions_one_active_per_user
        ON "{schema}".user_plan_subscriptions(user_id)
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f'DROP INDEX IF EXISTS "{schema}".idx_user_plan_subscriptions_one_active_per_user'
    )
