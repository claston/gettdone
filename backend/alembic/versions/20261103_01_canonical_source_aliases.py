"""Protect exact-document aliases without adding a second movement store."""
import os

from alembic import op
from app.application.canonical_movement_schema_v1 import canonical_movement_schema_v1

revision = "20261103_01"
down_revision = "20261102_02"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    canonical_movement_schema_v1(schema)
    op.execute(f'''CREATE OR REPLACE FUNCTION "{schema}".protect_canonical_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE canonical RECORD;
        BEGIN
            SELECT analysis_id, projection INTO canonical FROM "{schema}".canonical_analyses
                WHERE job_id=OLD.job_id OR
                    (OLD.result_payload->>'authority'='postgres_v1' AND analysis_id=OLD.result_analysis_id)
                ORDER BY (job_id=OLD.job_id) DESC LIMIT 1;
            IF FOUND AND (NEW.result_analysis_id IS DISTINCT FROM canonical.analysis_id
                OR (NEW.result_payload IS DISTINCT FROM OLD.result_payload
                    AND NEW.result_payload IS DISTINCT FROM canonical.projection)) THEN
                RAISE EXCEPTION 'Canonical analysis requires its SQL command adapter' USING ERRCODE='40001';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute(f'''CREATE OR REPLACE FUNCTION "{schema}".protect_canonical_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE canonical RECORD;
        BEGIN
            IF TG_OP='INSERT' THEN
                SELECT a.organization_id,a.client_id INTO canonical FROM "{schema}".canonical_analyses a
                    LEFT JOIN "{schema}".conversion_jobs j ON j.result_analysis_id=a.analysis_id
                    WHERE a.job_id=NEW.job_id OR (j.job_id=NEW.job_id AND j.result_payload->>'authority'='postgres_v1') LIMIT 1;
                IF FOUND AND NOT EXISTS (SELECT 1 FROM "{schema}".pro_conversion_contexts
                    WHERE organization_id=NEW.organization_id AND job_id=NEW.job_id)
                    AND (canonical.organization_id IS DISTINCT FROM NEW.organization_id
                        OR canonical.client_id IS DISTINCT FROM NEW.client_id OR NEW.account_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'Canonical scope reassignment requires a versioned command' USING ERRCODE='40001';
                END IF;
                RETURN NEW;
            END IF;
            IF EXISTS (SELECT 1 FROM "{schema}".canonical_analyses a
                LEFT JOIN "{schema}".conversion_jobs j ON j.result_analysis_id=a.analysis_id
                WHERE a.job_id=OLD.job_id OR (j.job_id=OLD.job_id AND j.result_payload->>'authority'='postgres_v1'))
                AND (TG_OP='DELETE' OR NEW.client_id IS DISTINCT FROM OLD.client_id OR NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
                RAISE EXCEPTION 'Canonical scope reassignment requires a versioned command' USING ERRCODE='40001';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$''')


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    canonical_movement_schema_v1(schema)
    op.execute(f'''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM "{schema}".conversion_jobs j
            JOIN "{schema}".canonical_analyses a ON a.analysis_id=j.result_analysis_id
            WHERE a.job_id<>j.job_id) THEN
            RAISE EXCEPTION 'Retain canonical alias protection until original sources expire';
        END IF;
    END $$''')
    # Restore the frozen pre-alias function, not the entire schema.
    for statement in canonical_movement_schema_v1(schema):
        if "CREATE OR REPLACE FUNCTION" in statement and any(
                name in statement for name in ("protect_canonical_job()", "protect_canonical_context()")):
            op.execute(statement)
