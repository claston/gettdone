"""Additive, retained delivery ledger for the first manual statement guide."""

import re


def marketing_campaign_schema_v1(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}marketing_campaigns (
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, text_template TEXT NOT NULL, html_template TEXT NOT NULL,
            from_email TEXT NOT NULL, public_base_url TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            lease_token TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '')""",
        f"""CREATE TABLE IF NOT EXISTS {prefix}marketing_campaign_deliveries (
            campaign_id TEXT NOT NULL REFERENCES {prefix}marketing_campaigns(id), user_id TEXT NOT NULL,
            email TEXT NOT NULL, consent_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','skipped','review')),
            payload TEXT, first_attempt_at TEXT, provider_message_id TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id,user_id), UNIQUE(campaign_id,email))""",
        f"CREATE INDEX IF NOT EXISTS ix_marketing_delivery_status ON {prefix}marketing_campaign_deliveries(campaign_id,status,user_id)",
    )
