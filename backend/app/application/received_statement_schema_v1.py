"""Additive receipt metadata; no PDF, transactions or report payload is retained here."""

import re

TABLES = ("pro_received_statements", "pro_received_statement_accounts")


def received_statement_schema_v1(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_received_statements (
            organization_id TEXT NOT NULL REFERENCES {prefix}pro_organizations(id),
            job_id TEXT NOT NULL, batch_id TEXT NOT NULL, filename TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            technical_status TEXT NOT NULL CHECK (technical_status IN ('ready', 'attention', 'processing')),
            source_json TEXT NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (organization_id, job_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS ix_received_expiry ON {prefix}pro_received_statements (organization_id, expires_at)",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_received_statement_accounts (
            organization_id TEXT NOT NULL, job_id TEXT NOT NULL, account_id TEXT NOT NULL,
            PRIMARY KEY (organization_id, job_id, account_id),
            FOREIGN KEY (organization_id, job_id) REFERENCES {prefix}pro_received_statements(organization_id, job_id),
            FOREIGN KEY (organization_id, account_id) REFERENCES {prefix}pro_accounts(organization_id, id)
        )""",
    )
