"""Office review metadata and opt-in job product; legacy workers default to Free."""
import os

from alembic import op
from app.application.office_schema import office_schema

revision = '20261122_01'
down_revision = '20261118_02'
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv('DATABASE_SCHEMA', 'public')
    statements = office_schema(schema)
    op.execute(f'''ALTER TABLE "{schema}".conversion_jobs
        ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT 'free' ''')
    for statement in statements:
        op.execute(statement)


def downgrade():
    # App rollback is safe with additive objects retained. Removing the product
    # marker would make paid documents run through Free during a rolling deploy.
    pass
