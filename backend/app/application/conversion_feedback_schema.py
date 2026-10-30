"""Shared additive schema for SQLite and PostgreSQL."""

import re


def feedback_schema(schema: str | None = None) -> tuple[str, ...]:
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name")
    table = f'"{schema}".conversion_feedback' if schema else "conversion_feedback"
    return (
        f"""CREATE TABLE IF NOT EXISTS {table} (
            processing_id TEXT NOT NULL,
            identity_type TEXT NOT NULL CHECK (identity_type IN ('registered', 'anonymous')),
            identity_id TEXT NOT NULL,
            offered_at TEXT NOT NULL,
            answer TEXT CHECK (answer IN ('yes', 'no')),
            answered_at TEXT,
            PRIMARY KEY (processing_id, identity_type, identity_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_conversion_feedback_offered ON {table}(offered_at, identity_type)",
    )
