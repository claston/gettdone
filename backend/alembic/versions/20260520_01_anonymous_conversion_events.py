"""Add anonymous conversion events table.

Revision ID: 20260520_01
Revises: 20260508_02
Create Date: 2026-05-20 00:00:00.000000
"""

from __future__ import annotations

import os

import sqlalchemy as sa

from alembic import op

revision = "20260520_01"
down_revision = "20260508_02"
branch_labels = None
depends_on = None


def _schema() -> str:
    return os.getenv("DATABASE_SCHEMA", "public").strip() or "public"


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "anonymous_conversion_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("anonymous_fingerprint", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("conversion_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("transactions_count", sa.Integer(), nullable=True),
        sa.Column("pages_count", sa.Integer(), nullable=True),
        sa.Column("scanned_likely", sa.Boolean(), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ocr_pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_anonymous_conversion_events_created_at
        ON "{schema}".anonymous_conversion_events(created_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_anonymous_conversion_events_status_created_at
        ON "{schema}".anonymous_conversion_events(status, created_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_anonymous_conversion_events_ocr_created_at
        ON "{schema}".anonymous_conversion_events(ocr_used, created_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_anonymous_conversion_events_conversion_type_created_at
        ON "{schema}".anonymous_conversion_events(conversion_type, created_at DESC)
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("anonymous_conversion_events", schema=schema)
