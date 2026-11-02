"""Frozen additive PostgreSQL DDL for OFX-69. No runtime schema bootstrap."""
import re


def canonical_movement_schema_v1(schema="public"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    p = f'"{schema}".'
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}canonical_analyses (
            analysis_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE REFERENCES {p}conversion_jobs(job_id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES {p}pro_organizations(id),
            client_id TEXT,
            authority TEXT NOT NULL DEFAULT 'postgres_v1' CHECK (authority = 'postgres_v1'),
            version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
            currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            metadata JSONB NOT NULL, baseline JSONB NOT NULL, ingestion_hash TEXT NOT NULL, projection JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (organization_id, analysis_id),
            FOREIGN KEY (organization_id, client_id) REFERENCES {p}pro_clients(organization_id, id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}bank_movements (
            id UUID PRIMARY KEY, organization_id TEXT NOT NULL, analysis_id TEXT NOT NULL,
            legacy_id TEXT NOT NULL, client_id TEXT, account_id TEXT,
            movement_date DATE NOT NULL, amount NUMERIC(18,2) NOT NULL,
            currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'), description TEXT NOT NULL,
            review_state TEXT NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending','reviewed')),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE, version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
            ordinal INTEGER NOT NULL, provenance JSONB NOT NULL, baseline JSONB, attributes JSONB NOT NULL,
            UNIQUE (analysis_id, legacy_id), UNIQUE (organization_id, analysis_id, id),
            FOREIGN KEY (organization_id, analysis_id)
                REFERENCES {p}canonical_analyses(organization_id, analysis_id) ON DELETE CASCADE,
            FOREIGN KEY (organization_id, client_id) REFERENCES {p}pro_clients(organization_id, id),
            FOREIGN KEY (organization_id, account_id) REFERENCES {p}pro_accounts(organization_id, id),
            CHECK (account_id IS NULL OR client_id IS NOT NULL),
            CHECK (amount != 'NaN'::numeric)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}movement_revisions (
            analysis_id TEXT NOT NULL REFERENCES {p}canonical_analyses(analysis_id) ON DELETE CASCADE,
            movement_id UUID NOT NULL REFERENCES {p}bank_movements(id) ON DELETE CASCADE,
            version BIGINT NOT NULL, operation_id TEXT NOT NULL, actor_id TEXT NOT NULL,
            channel TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            before_value JSONB, after_value JSONB NOT NULL,
            PRIMARY KEY (movement_id, version)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}movement_operations (
            analysis_id TEXT NOT NULL REFERENCES {p}canonical_analyses(analysis_id) ON DELETE CASCADE,
            operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, actor_id TEXT NOT NULL, channel TEXT NOT NULL,
            version BIGINT NOT NULL, response JSONB NOT NULL, metadata_change JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (analysis_id, operation_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}movement_artifacts (
            analysis_id TEXT NOT NULL REFERENCES {p}canonical_analyses(analysis_id) ON DELETE CASCADE,
            version BIGINT NOT NULL, manifest JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY (analysis_id, version)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}canonical_artifact_cleanup (
            analysis_id TEXT PRIMARY KEY, queued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )""",
        f"CREATE INDEX IF NOT EXISTS ix_canonical_expiry ON {p}canonical_analyses(expires_at)",
        f"CREATE INDEX IF NOT EXISTS ix_movement_client ON {p}bank_movements(organization_id, client_id, movement_date, id)",
        f"CREATE INDEX IF NOT EXISTS ix_movement_account ON {p}bank_movements(organization_id, account_id, movement_date, id)",
        f"""CREATE OR REPLACE FUNCTION {p}protect_movement_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN RETURN OLD; END IF;
            RAISE EXCEPTION 'Canonical history is append-only during authorized retention';
        END $$""",
        *tuple(f"""CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {p}{table}
            FOR EACH ROW EXECUTE FUNCTION {p}protect_movement_history()"""
               for table in ("movement_revisions", "movement_operations", "movement_artifacts")),
        f"""CREATE OR REPLACE FUNCTION {p}protect_movement_baseline() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.baseline IS DISTINCT FROM OLD.baseline THEN
                RAISE EXCEPTION 'Extraction baseline is immutable';
            END IF;
            RETURN NEW;
        END $$""",
        *tuple(f"""CREATE TRIGGER immutable_baseline BEFORE UPDATE ON {p}{table}
            FOR EACH ROW EXECUTE FUNCTION {p}protect_movement_baseline()"""
               for table in ("canonical_analyses", "bank_movements")),
        f"""CREATE OR REPLACE FUNCTION {p}protect_canonical_job() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE canonical RECORD;
        BEGIN
            SELECT analysis_id, projection INTO canonical FROM {p}canonical_analyses WHERE job_id = OLD.job_id;
            IF FOUND AND (NEW.result_analysis_id IS DISTINCT FROM canonical.analysis_id
                OR NEW.result_payload IS DISTINCT FROM canonical.projection) THEN
                RAISE EXCEPTION 'Canonical analysis requires its SQL command adapter' USING ERRCODE = '40001';
            END IF;
            RETURN NEW;
        END $$""",
        f"""CREATE TRIGGER canonical_job_authority BEFORE UPDATE ON {p}conversion_jobs
            FOR EACH ROW EXECUTE FUNCTION {p}protect_canonical_job()""",
        f"""CREATE OR REPLACE FUNCTION {p}protect_canonical_context() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (SELECT 1 FROM {p}pro_conversion_contexts
                    WHERE organization_id = NEW.organization_id AND job_id = NEW.job_id)
                    AND EXISTS (SELECT 1 FROM {p}canonical_analyses WHERE job_id = NEW.job_id
                    AND (organization_id IS DISTINCT FROM NEW.organization_id
                        OR client_id IS DISTINCT FROM NEW.client_id OR NEW.account_id IS NOT NULL)) THEN
                    RAISE EXCEPTION 'Canonical scope reassignment requires a versioned command' USING ERRCODE = '40001';
                END IF;
                RETURN NEW;
            END IF;
            IF EXISTS (SELECT 1 FROM {p}canonical_analyses WHERE job_id = OLD.job_id)
                AND (TG_OP = 'DELETE' OR NEW.client_id IS DISTINCT FROM OLD.client_id
                    OR NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
                RAISE EXCEPTION 'Canonical scope reassignment requires a versioned command' USING ERRCODE = '40001';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$""",
        f"""CREATE TRIGGER canonical_context_scope BEFORE INSERT OR UPDATE OR DELETE ON {p}pro_conversion_contexts
            FOR EACH ROW EXECUTE FUNCTION {p}protect_canonical_context()""",
        f"""CREATE OR REPLACE FUNCTION {p}protect_canonical_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.analysis_id IS DISTINCT FROM OLD.analysis_id OR NEW.job_id IS DISTINCT FROM OLD.job_id
                OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.client_id IS DISTINCT FROM OLD.client_id
                OR NEW.currency IS DISTINCT FROM OLD.currency OR NEW.expires_at > OLD.expires_at THEN
                RAISE EXCEPTION 'Canonical scope and retention cannot be expanded';
            END IF;
            RETURN NEW;
        END $$""",
        f"""CREATE TRIGGER canonical_scope_immutable BEFORE UPDATE ON {p}canonical_analyses
            FOR EACH ROW EXECUTE FUNCTION {p}protect_canonical_scope()""",
        f"""CREATE OR REPLACE FUNCTION {p}queue_canonical_artifact_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO {p}canonical_artifact_cleanup (analysis_id) VALUES (OLD.analysis_id) ON CONFLICT DO NOTHING;
            RETURN OLD;
        END $$""",
        f"""CREATE TRIGGER canonical_cleanup_after_delete AFTER DELETE ON {p}canonical_analyses
            FOR EACH ROW EXECUTE FUNCTION {p}queue_canonical_artifact_cleanup()""",
    )
