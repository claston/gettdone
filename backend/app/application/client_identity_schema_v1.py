"""Durable confirmation marker, without duplicating personal or financial data."""

import re


def client_identity_schema_v1(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    prefix = f'"{schema}".' if schema else ""
    return (f"""CREATE TABLE IF NOT EXISTS {prefix}pro_client_identity_confirmations (
        organization_id TEXT NOT NULL, client_id TEXT NOT NULL, confirmed_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, client_id),
        FOREIGN KEY (organization_id, client_id) REFERENCES {prefix}pro_clients(organization_id, id)
    )""",)


def protect_existing_client_identities(schema=None):
    client_identity_schema_v1(schema)
    prefix = f'"{schema}".' if schema else ""
    # Old versions did not distinguish a manual edit from an inference. Protect
    # every pre-upgrade identity conservatively instead of guessing its origin.
    return (f"INSERT INTO {prefix}pro_client_identity_confirmations (organization_id, client_id, confirmed_at) "
            f"SELECT organization_id, id, updated_at FROM {prefix}pro_clients WHERE 1 = 1 "
            "ON CONFLICT (organization_id, client_id) DO NOTHING")
