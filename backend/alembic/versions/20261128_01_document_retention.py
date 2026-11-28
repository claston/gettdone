"""Preserve PRO documents through paid access and the following thirty days."""
import os

from alembic import op
from app.application.document_retention_schema import document_retention_schema

revision = "20261128_01"
down_revision = "20261125_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in document_retention_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    # Keep lifetime guards on application rollback; never shorten paid retention.
    # Old API/worker schemas remain compatible. Re-upgrade is idempotent.
    pass
