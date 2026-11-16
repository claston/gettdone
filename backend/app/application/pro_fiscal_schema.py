"""Additive PRO fiscal schema. Bank movements remain the sole cash authority."""

import re


def pro_fiscal_schema(schema="public"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    p = f'"{schema}".'
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}pro_fiscal_documents (
            id UUID PRIMARY KEY, organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
            identity_key TEXT, payload JSONB NOT NULL, version BIGINT NOT NULL DEFAULT 1 CHECK(version>0),
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(organization_id, client_id, identity_key), UNIQUE(organization_id,client_id,id),
            FOREIGN KEY(organization_id,client_id) REFERENCES {p}pro_clients(organization_id,id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_fiscal_imports (
            id UUID PRIMARY KEY, organization_id TEXT NOT NULL, client_id TEXT NOT NULL, actor_id TEXT NOT NULL,
            document_id UUID, reference JSONB NOT NULL, digest TEXT NOT NULL, extraction JSONB,
            state TEXT NOT NULL CHECK(state IN ('uploading','queued','processing','ready','failed')),
            error TEXT, attempts INTEGER NOT NULL DEFAULT 0, lease_until TIMESTAMPTZ,
            next_dispatch_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(organization_id,client_id,digest),
            FOREIGN KEY(organization_id,client_id) REFERENCES {p}pro_clients(organization_id,id),
            FOREIGN KEY(organization_id,client_id,document_id) REFERENCES {p}pro_fiscal_documents(organization_id,client_id,id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_movement_classifications (
            movement_id UUID PRIMARY KEY REFERENCES {p}bank_movements(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
            category TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{{}}',
            movement_version BIGINT NOT NULL, version BIGINT NOT NULL DEFAULT 1 CHECK(version>0),
            actor_id TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(organization_id,client_id) REFERENCES {p}pro_clients(organization_id,id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_fiscal_links (
            id UUID PRIMARY KEY, organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
            document_id UUID NOT NULL, movement_id UUID NOT NULL REFERENCES {p}bank_movements(id) ON DELETE CASCADE,
            amount NUMERIC(18,2) NOT NULL CHECK(amount>0 AND amount<>'NaN'::numeric),
            document_version BIGINT NOT NULL, movement_version BIGINT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('confirmed','undone')), version BIGINT NOT NULL DEFAULT 1,
            actor_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(organization_id,client_id,document_id)
                REFERENCES {p}pro_fiscal_documents(organization_id,client_id,id) ON DELETE CASCADE
        )""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS ux_pro_fiscal_active_pair
            ON {p}pro_fiscal_links(document_id,movement_id) WHERE state='confirmed' """,
        f"""CREATE INDEX IF NOT EXISTS ix_pro_fiscal_documents_scope ON {p}pro_fiscal_documents(organization_id,client_id,expires_at)""",
        f"""CREATE INDEX IF NOT EXISTS ix_pro_fiscal_import_dispatch ON {p}pro_fiscal_imports(state,next_dispatch_at,expires_at)""",
        f"""CREATE INDEX IF NOT EXISTS ix_pro_fiscal_links_movement ON {p}pro_fiscal_links(movement_id)""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_fiscal_operations (
            organization_id TEXT NOT NULL, client_id TEXT NOT NULL, operation_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, actor_id TEXT NOT NULL, result JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(organization_id,operation_id),
            FOREIGN KEY(organization_id,client_id) REFERENCES {p}pro_clients(organization_id,id)
        )""",
    )
