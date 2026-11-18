"""Add isolated classification experiments; preserve evidence on app rollback."""
import os

from alembic import op
from app.application.classification_shadow.schema import shadow_schema

revision = "20261117_01"
down_revision = "20261116_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in shadow_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    # These tables have no FK into pre-existing tables. Retain experiments and
    # evaluations for application rollback; upgrade remains idempotent.
    shadow_schema(os.getenv("DATABASE_SCHEMA", "public"))
