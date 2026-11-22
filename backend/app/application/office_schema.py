"""Additive office metadata. No financial rows and no extended retention."""
import re


def office_schema(schema=None):
    # Retained metadata has an independent expiry. Tenant membership is checked
    # on every read/write; no foreign key pins the older organization migration
    # during a deep rollback or cascades away the audit trail.
    if schema is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', schema):
        raise ValueError('Invalid database schema')
    prefix = f'"{schema}".' if schema else ''
    return (
        f'''CREATE TABLE IF NOT EXISTS {prefix}office_document_associations (
            organization_id TEXT NOT NULL, job_id TEXT NOT NULL,
            version INTEGER NOT NULL, client_id TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, job_id, version)
        )''',
        f'CREATE INDEX IF NOT EXISTS ix_office_associations_expiry ON {prefix}office_document_associations(expires_at)',
        f'''CREATE TABLE IF NOT EXISTS {prefix}office_commercial_events (
            operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, actor_kind TEXT NOT NULL,
            actor_user_id TEXT, event_type TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
        )''',
        f'''CREATE TABLE IF NOT EXISTS {prefix}pro_document_reviews (
            organization_id TEXT NOT NULL,
            job_id TEXT NOT NULL, source TEXT NOT NULL, version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'verified', 'attention', 'unprocessable')),
            document_type TEXT, actor TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, job_id)
        )''',
        f'''CREATE TABLE IF NOT EXISTS {prefix}pro_document_review_events (
            organization_id TEXT NOT NULL,
            operation_id TEXT NOT NULL, job_id TEXT NOT NULL, request_hash TEXT NOT NULL,
            result_json TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, operation_id)
        )''',
        f'CREATE INDEX IF NOT EXISTS ix_office_reviews_expiry ON {prefix}pro_document_reviews(expires_at)',
        f'CREATE INDEX IF NOT EXISTS ix_office_events_expiry ON {prefix}pro_document_review_events(expires_at)',
    )
