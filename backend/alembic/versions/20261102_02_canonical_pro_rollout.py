"""Persistent opt-in PRO cohorts and per-analysis read/edit controls."""
import os

from alembic import op
from app.application.canonical_movement_schema_v1 import canonical_movement_schema_v1

revision = "20261102_02"
down_revision = "20261102_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    canonical_movement_schema_v1(schema)  # Validate identifier; do not rerun frozen DDL.
    op.execute(f'''CREATE TABLE "{schema}".canonical_pro_cohorts (
        organization_id TEXT PRIMARY KEY REFERENCES "{schema}".pro_organizations(id),
        ingestion_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        read_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        edit_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        ui_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        shadow_enabled BOOLEAN NOT NULL DEFAULT FALSE)''')
    for flag in ("read_enabled", "edit_enabled", "ui_enabled"):
        op.execute(f'ALTER TABLE "{schema}".canonical_analyses ADD COLUMN {flag} BOOLEAN NOT NULL DEFAULT FALSE')


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    canonical_movement_schema_v1(schema)
    op.execute(f'''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM "{schema}".canonical_analyses) THEN
            RAISE EXCEPTION 'Canonical authority cannot be downgraded; retain PRO routing until adopted documents expire';
        END IF;
    END $$''')
    for flag in ("read_enabled", "edit_enabled", "ui_enabled"):
        op.execute(f'ALTER TABLE "{schema}".canonical_analyses DROP COLUMN {flag}')
    op.execute(f'DROP TABLE "{schema}".canonical_pro_cohorts')
