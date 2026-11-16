"""Add a durable manual marketing campaign ledger without changing existing writers."""

import os

from alembic import op
from app.application.marketing_campaign_schema_v1 import marketing_campaign_schema_v1

revision = "20261115_01"
down_revision = "20261112_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in marketing_campaign_schema_v1(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade():
    # Retain deduplication evidence on application rollback. Re-upgrade is idempotent.
    # Dropping this ledger could resend a campaign that recipients already received.
    pass
