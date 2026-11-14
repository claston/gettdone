"""Add purchase notifications and administrator-provisioned first access.

Keep additive objects on rollback. Pending users are also inactive for old code.
"""
import os

from alembic import op
from app.application.purchase_schema import purchase_schema

revision = "20261112_01"
down_revision = "20261109_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    statements = purchase_schema(schema)
    for column in ("initial_access_pending BOOLEAN NOT NULL DEFAULT FALSE",
                   "account_creation_source TEXT",):
        op.execute(f'ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS {column}')
    op.execute(f'ALTER TABLE "{schema}".checkout_intents ADD COLUMN IF NOT EXISTS terms_accepted_at TEXT')
    for statement in statements:
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    purchase_schema(schema)
    op.execute(f'''DO $$ BEGIN
        LOCK TABLE "{schema}".users IN SHARE MODE;
        IF EXISTS (SELECT 1 FROM "{schema}".users WHERE initial_access_pending) THEN
            RAISE EXCEPTION 'Pending first access accounts exist; retain guards and schema. Disable purchase creation instead.';
        END IF;
    END $$''')
