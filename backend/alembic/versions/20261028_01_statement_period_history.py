"""Preserve documentary periods after conversion results expire."""

import os
import re

from alembic import op

revision = "20261028_01"
down_revision = "20261027_01"
branch_labels = None
depends_on = None


def _schema():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
    return schema


def upgrade():
    schema = _schema()
    # Nullable columns without defaults: existing producers and rows remain valid.
    # No PDF reprocessing, data backfill, new indexes or full-table updates.
    for table in ("user_conversions", "anonymous_conversion_events"):
        for column in ("statement_period_start", "statement_period_end", "statement_period_basis"):
            op.execute(f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS {column} TEXT')


def downgrade():
    # Application rollback retains evidence and permits old/new workers to coexist.
    # Reapplying upgrade is idempotent. Deliberate data removal is a separate operation.
    _schema()
