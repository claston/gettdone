"""Keep minimal received-statement history independently from expiring job payloads."""

import os

from alembic import op
from app.application.received_statement_schema_v1 import TABLES, received_statement_schema_v1

revision = "20261028_02"
down_revision = "20261028_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in received_statement_schema_v1(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    received_statement_schema_v1(schema)
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE IF EXISTS "{schema}".{table}')
