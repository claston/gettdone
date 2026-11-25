"""Retained documentary references; existing financial records are unchanged."""
import os

from alembic import op
from app.application.document_conference_schema import document_conference_schema

revision = "20261124_01"
down_revision = "20261123_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in document_conference_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    # Old application versions ignore these independent, expiring tables.
    # Retain references and audit for a safe application rollback/re-upgrade.
    pass
