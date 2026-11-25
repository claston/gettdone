"""Additive documentary relationships, independent of fiscal cash allocations."""
import re


def document_conference_schema(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    p = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}pro_document_movement_links (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
            job_id TEXT NOT NULL, movement_id TEXT NOT NULL, source TEXT NOT NULL,
            movement_version INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL CHECK(state IN ('confirmed','undone')),
            actor_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS ux_document_movement_active
            ON {p}pro_document_movement_links(organization_id,job_id,movement_id) WHERE state='confirmed'""",
        f"""CREATE INDEX IF NOT EXISTS ix_document_movement_scope
            ON {p}pro_document_movement_links(organization_id,client_id,movement_id)""",
        f"CREATE INDEX IF NOT EXISTS ix_document_movement_expiry ON {p}pro_document_movement_links(expires_at)",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_document_link_events (
            organization_id TEXT NOT NULL, operation_id TEXT NOT NULL, client_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, actor_id TEXT NOT NULL, result_json TEXT NOT NULL,
            expires_at TEXT NOT NULL, PRIMARY KEY(organization_id,operation_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS ix_document_link_event_expiry ON {p}pro_document_link_events(expires_at)",
    )
