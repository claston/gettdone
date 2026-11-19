"""Add shared, short-lived password login budgets without changing user records."""
import os

from alembic import op
from app.application.login_rate_limit import login_rate_limit_schema

revision = "20261118_01"
down_revision = "20261116_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in login_rate_limit_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    # Retain the additive table and active budgets for rolling application rollback.
    # Older writers ignore it; re-upgrade must not reset an attacker's budget.
    pass
