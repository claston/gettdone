"""Atomic public processing budgets shared by every API instance.

Budgets are independent of anonymous cookies. Network keys are keyed hashes,
never identities or report owners; users behind NAT cannot see each other's files.
"""
import hashlib
import hmac
import ipaddress
import os
import re


def public_budget_schema(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid PostgreSQL schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}public_request_budgets (
            budget_key TEXT PRIMARY KEY, window_started_at BIGINT NOT NULL,
            attempts INTEGER NOT NULL CHECK(attempts > 0), expires_at BIGINT NOT NULL
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_public_budget_expiry ON {prefix}public_request_budgets(expires_at)",
        f"""CREATE TABLE IF NOT EXISTS {prefix}public_budget_operations (
            operation_key TEXT PRIMARY KEY, expires_at BIGINT NOT NULL
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_public_operation_expiry ON {prefix}public_budget_operations(expires_at)",
    )


class PublicBudgetExceeded(Exception):
    def __init__(self, retry_after, *, scope):
        self.retry_after = retry_after
        self.scope = scope
        super().__init__("Public request budget exceeded")


def _network(value):
    try:
        address = ipaddress.ip_address(value or "")
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ipaddress.ip_network(f"{address}/64", strict=False))
        return str(address)
    except ValueError:
        # Missing/invalid proxy client metadata must not disable the budget.
        return "unknown"


def enforce_public_budget(service, *, kind, ip_address, units=1, operation_id=None):
    if kind == "conversion":
        window = service.quota_window_days * 86400
        network_limit = max(1, int(os.getenv("PUBLIC_CONVERSION_NETWORK_LIMIT", service.anonymous_quota_limit)))
        global_limit = max(1, int(os.getenv("PUBLIC_CONVERSION_HOURLY_LIMIT", "1000")))
        global_window = 3600
    elif kind == "session":
        window, network_limit, global_window, global_limit = 60, 10, 60, 1000
    else:
        raise ValueError("Unsupported public budget kind")
    if not isinstance(units, int) or units < 1:
        raise ValueError("Budget units must be a positive integer")
    now = int(service.now_provider().timestamp())

    def digest(value):
        return hmac.new(service.token_secret, f"public-budget:{kind}:{value}".encode(), hashlib.sha256).hexdigest()

    with service._lock, service._connect() as conn:
        if not service._use_postgres:
            service._execute(conn, "BEGIN IMMEDIATE")
        service._execute(conn, "DELETE FROM public_request_budgets WHERE expires_at <= ?", (now,))
        service._execute(conn, "DELETE FROM public_budget_operations WHERE expires_at <= ?", (now,))
        if operation_id:
            operation = service._fetchone(conn, """
                INSERT INTO public_budget_operations(operation_key, expires_at) VALUES (?, ?)
                ON CONFLICT(operation_key) DO NOTHING RETURNING operation_key
                """, (digest(f"operation:{operation_id}"), now - now % window + window))
            if operation is None:
                conn.commit()
                return
        # Stable lock order prevents deadlocks across instances.
        for scope, value, seconds, limit in (
            ("global", "all", global_window, global_limit),
            ("network", _network(ip_address), window, network_limit),
        ):
            start = now - now % seconds
            row = service._fetchone(conn, """
                INSERT INTO public_request_budgets(budget_key, window_started_at, attempts, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(budget_key) DO UPDATE SET
                    attempts = CASE WHEN public_request_budgets.window_started_at < excluded.window_started_at
                        THEN excluded.attempts ELSE public_request_budgets.attempts + excluded.attempts END,
                    window_started_at = excluded.window_started_at, expires_at = excluded.expires_at
                RETURNING attempts
                """, (digest(f"{scope}:{value}"), start, units, start + seconds))
            if int(row["attempts"]) > limit:
                conn.rollback()
                raise PublicBudgetExceeded(max(1, start + seconds - now), scope=scope)
        conn.commit()
