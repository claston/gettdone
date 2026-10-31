"""Additive, durable accounting for explicitly requested paid OCR."""

import re


def conversion_pro_schema(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    table = f'"{schema}".conversion_pro_usage' if schema else "conversion_pro_usage"
    return (
        f"""CREATE TABLE IF NOT EXISTS {table} (
            job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, period TEXT NOT NULL,
            reserved_pages INTEGER NOT NULL CHECK (reserved_pages > 0),
            attempted_pages INTEGER NOT NULL DEFAULT 0 CHECK (attempted_pages >= 0 AND attempted_pages <= reserved_pages),
            status TEXT NOT NULL CHECK (status IN ('reserved','finished')),
            created_at TEXT NOT NULL
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_conversion_pro_usage_owner ON {table}(owner_id,period)",
    )
