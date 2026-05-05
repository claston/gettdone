"""add pricing catalog and user plan subscriptions

Revision ID: 20260504_01
Revises: 20260429_01
Create Date: 2026-05-04 00:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260504_01"
down_revision: Union[str, Sequence[str], None] = "20260429_01"
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
        CREATE TABLE IF NOT EXISTS "{schema}".plan_versions (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            currency TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            billing_period TEXT NOT NULL,
            quota_mode TEXT NOT NULL,
            quota_limit INTEGER NOT NULL,
            quota_window_days INTEGER NOT NULL,
            max_upload_size_bytes INTEGER NOT NULL,
            max_pages_per_file INTEGER NOT NULL,
            is_public BOOLEAN NOT NULL DEFAULT TRUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".user_plan_subscriptions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES "{schema}".users(id),
            plan_version_id TEXT NOT NULL REFERENCES "{schema}".plan_versions(id),
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_versions_code_version
        ON "{schema}".plan_versions(code, version)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_plan_subscriptions_user_active
        ON "{schema}".user_plan_subscriptions(user_id, status)
        """
    )
    op.execute(
        f"""
        INSERT INTO "{schema}".plan_versions (
            id,
            code,
            name,
            version,
            currency,
            price_cents,
            billing_period,
            quota_mode,
            quota_limit,
            quota_window_days,
            max_upload_size_bytes,
            max_pages_per_file,
            is_public,
            is_active,
            created_at
        ) VALUES
            (
                'plan_essencial_v1',
                'essencial',
                'Essencial',
                1,
                'BRL',
                2990,
                'monthly',
                'pages',
                150,
                30,
                10485760,
                100,
                TRUE,
                TRUE,
                NOW()::text
            ),
            (
                'plan_profissional_v1',
                'profissional',
                'Profissional',
                1,
                'BRL',
                3990,
                'monthly',
                'pages',
                300,
                30,
                10485760,
                100,
                TRUE,
                TRUE,
                NOW()::text
            ),
            (
                'plan_escritorio_v1',
                'escritorio',
                'Escritorio',
                1,
                'BRL',
                4990,
                'monthly',
                'pages',
                500,
                30,
                10485760,
                100,
                TRUE,
                TRUE,
                NOW()::text
            )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(f'DROP TABLE IF EXISTS "{schema}".user_plan_subscriptions')
    op.execute(f'DROP TABLE IF EXISTS "{schema}".plan_versions')
