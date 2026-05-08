"""align postgres schema with access-control runtime bootstrap

Revision ID: 20260508_02
Revises: 20260508_01
Create Date: 2026-05-08 03:20:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260508_02"
down_revision: Union[str, Sequence[str], None] = "20260508_01"
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
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".user_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES "{schema}".users(id),
            refresh_token_hash TEXT NOT NULL UNIQUE,
            refresh_token_family TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            rotated_at TEXT,
            revoked_at TEXT,
            replaced_by_session_id TEXT,
            revoke_reason TEXT,
            last_ip TEXT,
            last_user_agent TEXT
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".checkout_intent_events (
            id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL REFERENCES "{schema}".checkout_intents(id),
            event_type TEXT NOT NULL,
            event_message TEXT,
            actor_kind TEXT NOT NULL,
            actor_user_id TEXT REFERENCES "{schema}".users(id),
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".admin_user_role_events (
            id TEXT PRIMARY KEY,
            target_user_id TEXT NOT NULL REFERENCES "{schema}".users(id),
            target_email TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_user_id TEXT REFERENCES "{schema}".users(id),
            actor_email TEXT,
            previous_is_admin BOOLEAN NOT NULL,
            new_is_admin BOOLEAN NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    op.execute(f'ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute(f'ALTER TABLE "{schema}".user_conversions ADD COLUMN IF NOT EXISTS pages_count INTEGER')
    op.execute(f'ALTER TABLE "{schema}".checkout_intents ADD COLUMN IF NOT EXISTS user_id TEXT')
    op.execute(f'ALTER TABLE "{schema}".checkout_intents ADD COLUMN IF NOT EXISTS payment_link TEXT')
    op.execute(f'ALTER TABLE "{schema}".checkout_intents ADD COLUMN IF NOT EXISTS payment_link_sent_at TEXT')
    op.execute(f'ALTER TABLE "{schema}".checkout_intents ADD COLUMN IF NOT EXISTS released_at TEXT')

    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_provider_user_id
        ON "{schema}".users(provider_user_id)
        WHERE auth_provider = 'google' AND provider_user_id IS NOT NULL
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_conversions_user_created_at
        ON "{schema}".user_conversions(user_id, created_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_user_created_at
        ON "{schema}".user_sessions(user_id, created_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_family
        ON "{schema}".user_sessions(refresh_token_family)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
        ON "{schema}".user_sessions(expires_at)
        """
    )
    op.execute(
        f"""
        UPDATE "{schema}".users
        SET auth_provider = 'local'
        WHERE auth_provider IS NULL OR auth_provider = ''
        """
    )
    op.execute(
        f"""
        UPDATE "{schema}".usage
        SET window_started_at = updated_at
        WHERE window_started_at IS NULL OR window_started_at = ''
        """
    )
    op.execute(
        f"""
        UPDATE "{schema}".checkout_intents
        SET status = 'REQUESTED'
        WHERE status = 'pending'
        """
    )


def downgrade() -> None:
    # Safe rollback for this transition is code rollback; schema reversal is intentionally no-op.
    return None
