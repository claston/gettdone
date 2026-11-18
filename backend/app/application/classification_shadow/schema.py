"""Frozen additive shadow schema, independent from live classifications."""
import re


def shadow_schema(schema="public"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    p = f'"{schema}".'
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}classification_shadow_runs (
            id UUID PRIMARY KEY, organization_id TEXT NOT NULL, client_id TEXT NOT NULL, actor_id TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK(source_kind IN ('movement','invoice')), source_id UUID NOT NULL,
            source_version BIGINT NOT NULL, engine_version TEXT NOT NULL, input_hash TEXT NOT NULL,
            request JSONB NOT NULL, result JSONB, state TEXT NOT NULL CHECK(state IN ('queued','ready','failed')),
            next_dispatch_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), dispatch_attempts INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), expires_at TIMESTAMPTZ NOT NULL,
            UNIQUE(organization_id,client_id,input_hash)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {p}classification_shadow_feedback (
            run_id UUID NOT NULL REFERENCES {p}classification_shadow_runs(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL, verdict TEXT NOT NULL CHECK(verdict IN ('agree','disagree','unknown')),
            comment TEXT NOT NULL DEFAULT '', actor_id TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(run_id,item_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS ix_shadow_expiry ON {p}classification_shadow_runs(expires_at)",
    )
