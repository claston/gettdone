"""Add password recovery tokens and legacy credential revocation.

Retain these additive objects on application rollback. Dropping credential
versions could restore compromised legacy tokens, so downgrade is blocked
once a password reset has revoked credentials.
"""
import os

from alembic import op
from app.application.password_reset_schema import password_reset_schema

revision = "20261109_01"
down_revision = "20261103_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    statements = password_reset_schema(schema)
    op.execute(f'ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS credential_version INTEGER NOT NULL DEFAULT 0')
    for statement in statements:
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    password_reset_schema(schema)  # Validate before interpolating an identifier.
    # Keep additive objects for old writers and idempotent re-upgrades. Serialize
    # the safety check with resets until the migration transaction completes.
    op.execute(f'''DO $$ BEGIN
        LOCK TABLE "{schema}".users IN SHARE MODE;
        IF EXISTS (SELECT 1 FROM "{schema}".users WHERE credential_version > 0) THEN
            RAISE EXCEPTION 'Retain password reset schema and credential revocation on rollback; disable recovery routes instead.';
        END IF;
    END $$''')
