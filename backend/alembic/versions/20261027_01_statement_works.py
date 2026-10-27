"""Add statement works, immutable closings and audit events."""

import os

from alembic import op
from app.application.statement_work_schema_v1 import TABLES, statement_work_schema_v1

revision = "20261027_01"
down_revision = "20261024_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in statement_work_schema_v1(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    statement_work_schema_v1(schema)
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE IF EXISTS "{schema}".{table}')
