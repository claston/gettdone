"""Additive lifetime guards, including compatibility with workers during rollout.

Expired documents are never resurrected. Only explicit PRO imports and retained
fiscal documents acquire paid lifetime. Free conversions retain their original TTL.
"""
import re

from app.application.black_friday import PAID_PLAN_CODES


def document_retention_schema(schema):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid schema")
    p = f'"{schema}".'
    codes = ",".join(f"'{code}'" for code in sorted(PAID_PLAN_CODES))
    statements = [
        f"ALTER TABLE {p}conversion_jobs ADD COLUMN IF NOT EXISTS pro_document_purged_at TIMESTAMPTZ",
        f"ALTER TABLE {p}conversion_jobs ADD COLUMN IF NOT EXISTS retained_analysis_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_document_retention_accounts (
            owner_id TEXT PRIMARY KEY, delete_after TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""",
        f"CREATE INDEX IF NOT EXISTS ix_pro_document_retention_expiry ON {p}pro_document_retention_accounts(delete_after)",
        f"""CREATE OR REPLACE FUNCTION {p}protect_canonical_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.analysis_id IS DISTINCT FROM OLD.analysis_id OR NEW.job_id IS DISTINCT FROM OLD.job_id
                OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.client_id IS DISTINCT FROM OLD.client_id
                OR NEW.currency IS DISTINCT FROM OLD.currency THEN
                RAISE EXCEPTION 'Canonical scope cannot change';
            END IF;
            IF NEW.expires_at > OLD.expires_at AND NOT EXISTS (
                SELECT 1 FROM {p}conversion_jobs j JOIN {p}pro_document_retention_accounts r
                    ON r.owner_id=j.identity->>'identity_id'
                WHERE j.job_id=NEW.job_id AND j.product='pro' AND j.status<>'expired'
                    AND OLD.expires_at>clock_timestamp() AND NEW.expires_at<=j.expires_at AND NEW.expires_at<=r.delete_after) THEN
                RAISE EXCEPTION 'Canonical retention cannot exceed paid document lifetime';
            END IF;
            IF NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                NEW.metadata := jsonb_set(NEW.metadata,'{{expires_at}}',
                    to_jsonb(to_char(NEW.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00'));
            END IF;
            RETURN NEW;
        END $$""",
        f"""CREATE INDEX IF NOT EXISTS ix_pro_jobs_retention_owner ON {p}conversion_jobs
            ((identity->>'identity_id')) WHERE product='pro'""",
        f"""CREATE OR REPLACE FUNCTION {p}refresh_document_retention(actor TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
        DECLARE deadline TIMESTAMPTZ; previous TIMESTAMPTZ;
        BEGIN
            SELECT max(CASE WHEN s.ended_at IS NOT NULL THEN s.ended_at::timestamptz + interval '30 days'
                WHEN s.status='active' AND v.code<>'escritorio_documentos' THEN '9999-01-01T00:00:00Z'::timestamptz END)
            INTO deadline FROM {p}user_plan_subscriptions s JOIN {p}plan_versions v ON v.id=s.plan_version_id
            WHERE s.user_id=actor AND v.code IN ({codes}) AND v.batch_conversion_enabled
                AND s.started_at::timestamptz <= clock_timestamp();
            SELECT delete_after INTO previous FROM {p}pro_document_retention_accounts WHERE owner_id=actor FOR UPDATE;
            IF deadline IS NULL AND previous IS NULL THEN RETURN; END IF;
            deadline := coalesce(deadline, clock_timestamp());
            INSERT INTO {p}pro_document_retention_accounts(owner_id,delete_after) VALUES(actor,deadline)
            ON CONFLICT(owner_id) DO UPDATE SET delete_after=excluded.delete_after,updated_at=clock_timestamp();
            IF previous IS NOT DISTINCT FROM deadline THEN RETURN; END IF;
            UPDATE {p}conversion_jobs SET expires_at=deadline
                WHERE product='pro' AND identity->>'identity_type'='user' AND identity->>'identity_id'=actor
                AND expires_at>clock_timestamp() AND status<>'expired';
            UPDATE {p}conversion_batches b SET expires_at=deadline WHERE EXISTS (
                SELECT 1 FROM {p}conversion_jobs j WHERE j.batch_id=b.batch_id AND j.product='pro'
                AND j.identity->>'identity_id'=actor AND j.expires_at=deadline);
            UPDATE {p}pro_fiscal_imports SET expires_at=deadline WHERE actor_id=actor AND expires_at>clock_timestamp();
            UPDATE {p}pro_fiscal_documents d SET expires_at=deadline FROM {p}pro_organizations o
                WHERE d.organization_id=o.id AND o.owner_user_id=actor AND d.expires_at>clock_timestamp();
        END $$""",
        f"""CREATE OR REPLACE FUNCTION {p}subscription_document_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN PERFORM {p}refresh_document_retention(OLD.user_id); RETURN OLD; END IF;
            PERFORM {p}refresh_document_retention(NEW.user_id);
            RETURN NEW;
        END $$""",
        f"DROP TRIGGER IF EXISTS document_retention_subscription ON {p}user_plan_subscriptions",
        f"""CREATE TRIGGER document_retention_subscription AFTER INSERT OR UPDATE OR DELETE ON {p}user_plan_subscriptions
            FOR EACH ROW EXECUTE FUNCTION {p}subscription_document_retention()""",
        f"""CREATE OR REPLACE FUNCTION {p}guard_pro_job_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE deadline TIMESTAMPTZ;
        BEGIN
            IF NEW.product<>'pro' OR NEW.identity->>'identity_type'<>'user' THEN RETURN NEW; END IF;
            IF TG_OP='UPDATE' AND (OLD.expires_at<=clock_timestamp() OR OLD.status='expired') THEN
                NEW.expires_at := OLD.expires_at;
                IF OLD.status='expired' THEN NEW.status := OLD.status; END IF;
                RETURN NEW;
            END IF;
            SELECT delete_after INTO deadline FROM {p}pro_document_retention_accounts
                WHERE owner_id=NEW.identity->>'identity_id';
            IF deadline IS NOT NULL THEN
                NEW.expires_at := deadline;
                IF NEW.result_payload->'analysis' IS NOT NULL THEN
                    IF NOT (NEW.result_payload->'analysis' ? '_retention_original_expires_at') THEN
                        NEW.result_payload := jsonb_set(NEW.result_payload,'{{analysis,_retention_original_expires_at}}',
                            coalesce(NEW.result_payload#>'{{analysis,expires_at}}','null'::jsonb));
                    END IF;
                    NEW.result_payload := jsonb_set(NEW.result_payload,'{{analysis,expires_at}}',
                        to_jsonb(to_char(deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00'));
                END IF;
            END IF;
            RETURN NEW;
        END $$""",
        f"DROP TRIGGER IF EXISTS document_retention_job ON {p}conversion_jobs",
        f"""CREATE TRIGGER document_retention_job BEFORE INSERT OR UPDATE OF expires_at,result_payload ON {p}conversion_jobs
            FOR EACH ROW EXECUTE FUNCTION {p}guard_pro_job_retention()""",
        f"""CREATE OR REPLACE FUNCTION {p}guard_pro_batch_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE deadline TIMESTAMPTZ;
        BEGIN
            SELECT max(expires_at) INTO deadline FROM {p}conversion_jobs WHERE batch_id=NEW.batch_id AND product='pro';
            IF deadline IS NOT NULL THEN NEW.expires_at := deadline; END IF;
            RETURN NEW;
        END $$""",
        f"DROP TRIGGER IF EXISTS document_retention_batch ON {p}conversion_batches",
        f"""CREATE TRIGGER document_retention_batch BEFORE UPDATE OF expires_at ON {p}conversion_batches
            FOR EACH ROW EXECUTE FUNCTION {p}guard_pro_batch_retention()""",
    ]
    job_tables = ("canonical_analyses", "pro_received_statements", "office_document_associations",
                  "pro_document_reviews", "pro_document_review_events", "pro_document_movement_links")
    organization_tables = ("pro_fiscal_documents", "pro_fiscal_imports", "pro_fiscal_operations")
    statements += [
        f"""CREATE OR REPLACE FUNCTION {p}guard_document_metadata_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE deadline TIMESTAMPTZ;
        BEGIN
            IF TG_OP='UPDATE' AND OLD.expires_at::timestamptz<=clock_timestamp() THEN
                NEW.expires_at := OLD.expires_at; RETURN NEW;
            END IF;
            IF TG_ARGV[0]='job' THEN
                SELECT expires_at INTO deadline FROM {p}conversion_jobs WHERE job_id=NEW.job_id AND product='pro';
            ELSE
                SELECT r.delete_after INTO deadline FROM {p}pro_document_retention_accounts r
                JOIN {p}pro_organizations o ON o.owner_user_id=r.owner_id WHERE o.id=NEW.organization_id;
            END IF;
            IF deadline IS NOT NULL THEN
                NEW.expires_at := to_char(deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00';
                IF TG_TABLE_NAME='canonical_analyses' THEN
                    NEW.metadata := jsonb_set(NEW.metadata,'{{expires_at}}',
                        to_jsonb(to_char(deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00'));
                END IF;
            END IF;
            RETURN NEW;
        END $$""",
    ]
    for table in (*job_tables, *organization_tables):
        mode = "job" if table in job_tables else "organization"
        statements += [f"DROP TRIGGER IF EXISTS document_retention_metadata ON {p}{table}",
                       f"""CREATE TRIGGER document_retention_metadata BEFORE INSERT OR UPDATE OF expires_at ON {p}{table}
                           FOR EACH ROW EXECUTE FUNCTION {p}guard_document_metadata_retention('{mode}')"""]
    updates = "\n".join(f"""UPDATE {p}{table} SET expires_at=to_char(NEW.expires_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00' WHERE job_id=NEW.job_id AND expires_at::timestamptz>clock_timestamp();"""
        if table != "canonical_analyses" else f"""UPDATE {p}{table} SET expires_at=NEW.expires_at
            WHERE job_id=NEW.job_id AND expires_at>clock_timestamp();"""
        for table in job_tables)
    # Timestamp columns require an explicit timestamp, while older metadata uses ISO text.
    statements += [
        f"""CREATE OR REPLACE FUNCTION {p}propagate_document_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.product<>'pro' THEN RETURN NEW; END IF;
            UPDATE {p}conversion_batches SET expires_at=NEW.expires_at WHERE batch_id=NEW.batch_id;
            {updates}
            UPDATE {p}pro_document_link_events e SET expires_at=l.expires_at
                FROM {p}pro_document_movement_links l WHERE l.job_id=NEW.job_id
                AND e.organization_id=l.organization_id AND e.result_json::jsonb->>'id'=l.id
                AND e.expires_at::timestamptz>clock_timestamp();
            RETURN NEW;
        END $$""",
        f"DROP TRIGGER IF EXISTS document_retention_propagate ON {p}conversion_jobs",
        f"""CREATE TRIGGER document_retention_propagate AFTER INSERT OR UPDATE OF expires_at ON {p}conversion_jobs
            FOR EACH ROW EXECUTE FUNCTION {p}propagate_document_retention()""",
        f"""CREATE OR REPLACE FUNCTION {p}propagate_account_retention() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE {p}pro_fiscal_operations e SET expires_at=NEW.delete_after FROM {p}pro_organizations o
                WHERE e.organization_id=o.id AND o.owner_user_id=NEW.owner_id AND e.expires_at>clock_timestamp();
            RETURN NEW;
        END $$""",
        f"DROP TRIGGER IF EXISTS document_retention_account_propagate ON {p}pro_document_retention_accounts",
        f"""CREATE TRIGGER document_retention_account_propagate AFTER INSERT OR UPDATE OF delete_after ON {p}pro_document_retention_accounts
            FOR EACH ROW EXECUTE FUNCTION {p}propagate_account_retention()""",
        # Preserve only still-available documents during the initial additive migration.
        f"SELECT {p}refresh_document_retention(user_id) FROM (SELECT DISTINCT user_id FROM {p}user_plan_subscriptions) owners",
    ]
    return tuple(statements)
