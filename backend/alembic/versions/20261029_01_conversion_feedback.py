"""Add optional conversion feedback without changing existing writers."""

import os

from alembic import op
from app.application.conversion_feedback_schema import feedback_schema

revision = "20261029_01"
down_revision = "20261028_02"
branch_labels = None
depends_on = None


def upgrade():
    for statement in feedback_schema(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade():
    # Retain survey answers during application rollback. Old binaries ignore this table.
    pass
