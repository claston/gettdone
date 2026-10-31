"""Add paid OCR intent and durable per-page budget without changing old jobs."""

import os

from alembic import op
from app.application.conversion.pro_conversion_schema import conversion_pro_schema

revision = "20261031_01"
down_revision = "20261029_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    statements = conversion_pro_schema(schema)
    op.execute(f"ALTER TABLE \"{schema}\".conversion_jobs ADD COLUMN IF NOT EXISTS ocr_mode TEXT NOT NULL DEFAULT 'standard'")
    for statement in statements:
        op.execute(statement)


def downgrade():
    # Disable feature and drain PRO jobs first. Old code ignores additive fields/tables.
    # Preserve budget records to prevent duplicate paid consumption during rollback.
    pass
