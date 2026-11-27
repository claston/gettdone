"""Persist public request budgets independently of anonymous identities."""
import os

from alembic import op
from app.application.public_request_budget import public_budget_schema

revision = "20261125_01"
down_revision = "20261124_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in public_budget_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    # Preserve budgets during rolling rollback/re-upgrade. Old code ignores them.
    pass
