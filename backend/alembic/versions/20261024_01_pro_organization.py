"""Add private PRO clients, accounts, flexible periods and conversion links.

Revision ID: 20261024_01
Revises: 20261012_01
"""

import os

from alembic import op
from app.application.organization_schema_v1 import TABLES, organization_schema_v1

revision = "20261024_01"
down_revision = "20261012_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in organization_schema_v1(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade() -> None:
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    organization_schema_v1(schema)  # Validate before interpolating identifiers.
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE IF EXISTS "{schema}".{table}')
