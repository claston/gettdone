"""Immutable additive DDL for statement works, closure snapshots and audit events."""

import re

TABLES = ("pro_statement_works", "pro_statement_closings", "pro_statement_work_events")


def statement_work_schema_v1(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    p = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}pro_statement_works (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES {p}pro_organizations(id),
            client_id TEXT NOT NULL, period_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','completed')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0), settings TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES {p}users(id), updated_by TEXT NOT NULL REFERENCES {p}users(id),
            UNIQUE(organization_id, id), UNIQUE(organization_id, client_id, period_id),
            FOREIGN KEY(organization_id, client_id) REFERENCES {p}pro_clients(organization_id,id),
            FOREIGN KEY(organization_id, period_id) REFERENCES {p}pro_periods(organization_id,id))""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_statement_closings (
            organization_id TEXT NOT NULL, work_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
            snapshot TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES {p}users(id),
            PRIMARY KEY(organization_id, work_id, revision),
            FOREIGN KEY(organization_id, work_id) REFERENCES {p}pro_statement_works(organization_id,id))""",
        f"""CREATE TABLE IF NOT EXISTS {p}pro_statement_work_events (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, work_id TEXT NOT NULL, event TEXT NOT NULL,
            detail TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES {p}users(id),
            FOREIGN KEY(organization_id, work_id) REFERENCES {p}pro_statement_works(organization_id,id))""",
        f"CREATE INDEX IF NOT EXISTS ix_pro_works_period ON {p}pro_statement_works(organization_id,period_id,state)",
        f"CREATE INDEX IF NOT EXISTS ix_pro_work_events ON {p}pro_statement_work_events(organization_id,work_id,created_at)",
    )
