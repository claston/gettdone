"""Add canonical bank movements, retained baseline, CAS receipts and revisions."""
import os

from alembic import op
from app.application.canonical_movement_schema_v1 import canonical_movement_schema_v1

revision = "20261102_01"
down_revision = "20261101_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in canonical_movement_schema_v1(os.getenv("DATABASE_SCHEMA", "public").strip() or "public"):
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    canonical_movement_schema_v1(schema)
    # Before adoption, rollback is reversible. After adoption, fail transactionally
    # rather than deleting corrections or allowing a legacy writer to take over.
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM "{schema}".canonical_analyses)
            OR EXISTS (SELECT 1 FROM "{schema}".canonical_artifact_cleanup) THEN
            RAISE EXCEPTION 'Canonical authority cannot be downgraded; use a compatible application rollback';
        END IF;
    END $$""")
    op.execute(f'DROP TRIGGER canonical_job_authority ON "{schema}".conversion_jobs')
    op.execute(f'DROP TRIGGER canonical_context_scope ON "{schema}".pro_conversion_contexts')
    for table in ("movement_artifacts", "movement_operations", "movement_revisions", "bank_movements", "canonical_analyses",
                  "canonical_artifact_cleanup"):
        op.execute(f'DROP TABLE "{schema}".{table}')
    for function in ("protect_canonical_job", "protect_canonical_context", "protect_canonical_scope",
                     "protect_movement_baseline", "protect_movement_history", "queue_canonical_artifact_cleanup"):
        op.execute(f'DROP FUNCTION "{schema}".{function}()')
