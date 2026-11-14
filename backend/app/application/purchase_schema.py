import re


def purchase_schema(schema: str | None = None) -> tuple[str, ...]:
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid PostgreSQL schema")
    p = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {p}purchase_tokens (
            id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES {p}checkout_intents(id),
            user_id TEXT REFERENCES {p}users(id), purpose TEXT NOT NULL CHECK (purpose IN ('setup', 'payment')),
            token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            consumed_at TEXT, invalidated_at TEXT
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_purchase_tokens_intent ON {p}purchase_tokens(intent_id, purpose)",
        f"""CREATE TABLE IF NOT EXISTS {p}checkout_notifications (
            id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES {p}checkout_intents(id),
            kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0, provider_message_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (intent_id, kind)
        )""",
    )
