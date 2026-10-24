"""Immutable additive DDL shared by migration 20261024_01 and local SQLite bootstrap."""

import re

TABLES = ("pro_organizations", "pro_clients", "pro_accounts", "pro_periods", "pro_conversion_contexts", "pro_conversion_periods")


def organization_schema_v1(schema: str | None = None) -> tuple[str, ...]:
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_organizations (
            id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL UNIQUE REFERENCES {prefix}users(id),
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_clients (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES {prefix}pro_organizations(id),
            name TEXT NOT NULL, tax_id TEXT, trade_name TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL REFERENCES {prefix}users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (organization_id, id), UNIQUE (organization_id, tax_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_accounts (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES {prefix}pro_organizations(id),
            client_id TEXT NOT NULL, name TEXT NOT NULL, bank_code TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT '', account_number TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL REFERENCES {prefix}users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (organization_id, id),
            FOREIGN KEY (organization_id, client_id) REFERENCES {prefix}pro_clients(organization_id, id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_periods (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES {prefix}pro_organizations(id),
            name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES {prefix}users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (organization_id, id), CHECK (start_date <= end_date)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_conversion_contexts (
            organization_id TEXT NOT NULL REFERENCES {prefix}pro_organizations(id), job_id TEXT NOT NULL,
            client_id TEXT, account_id TEXT, version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_by TEXT NOT NULL REFERENCES {prefix}users(id), updated_by TEXT NOT NULL REFERENCES {prefix}users(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (organization_id, job_id),
            FOREIGN KEY (organization_id, client_id) REFERENCES {prefix}pro_clients(organization_id, id),
            FOREIGN KEY (organization_id, account_id) REFERENCES {prefix}pro_accounts(organization_id, id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}pro_conversion_periods (
            organization_id TEXT NOT NULL, job_id TEXT NOT NULL, period_id TEXT NOT NULL,
            PRIMARY KEY (organization_id, job_id, period_id),
            FOREIGN KEY (organization_id, job_id) REFERENCES {prefix}pro_conversion_contexts(organization_id, job_id) ON DELETE CASCADE,
            FOREIGN KEY (organization_id, period_id) REFERENCES {prefix}pro_periods(organization_id, id)
        )""",
        f"CREATE INDEX IF NOT EXISTS ix_pro_context_client ON {prefix}pro_conversion_contexts (organization_id, client_id)",
        f"CREATE INDEX IF NOT EXISTS ix_pro_context_account ON {prefix}pro_conversion_contexts (organization_id, account_id)",
        f"CREATE INDEX IF NOT EXISTS ix_pro_period_links ON {prefix}pro_conversion_periods (organization_id, period_id)",
    )
