import re


def password_reset_schema(schema: str | None = None) -> tuple[str, ...]:
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid PostgreSQL schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}password_reset_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES {prefix}users(id),
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            invalidated_at TEXT,
            delivery_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (delivery_status IN ('pending', 'sent', 'failed'))
        )""",
        f"""CREATE INDEX IF NOT EXISTS idx_password_reset_user_created
            ON {prefix}password_reset_tokens(user_id, created_at DESC)""",
    )
