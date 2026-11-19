"""Password-login budgets shared by API workers through the application database."""
import hashlib
import hmac
import re

WINDOW_SECONDS = 300
ACCOUNT_LIMIT = 10
IP_LIMIT = 60


def login_rate_limit_schema(schema=None):
    if schema is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid PostgreSQL schema")
    prefix = f'"{schema}".' if schema else ""
    return (
        f"""CREATE TABLE IF NOT EXISTS {prefix}password_login_budgets (
            budget_key TEXT PRIMARY KEY, window_started_at BIGINT NOT NULL,
            attempts INTEGER NOT NULL CHECK(attempts > 0)
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_password_login_budget_window ON {prefix}password_login_budgets(window_started_at)",
    )


class LoginRateLimitedError(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Password login rate limit exceeded")


def enforce_password_login_budget(service, *, email, ip_address):
    now = int(service.now_provider().timestamp())
    start = now - now % WINDOW_SECONDS
    rejected = False
    with service._lock, service._connect() as conn:
        service._execute(conn, "DELETE FROM password_login_budgets WHERE window_started_at < ?", (start - 2 * WINDOW_SECONDS,))
        for kind, value, limit in (("ip", ip_address or "unknown", IP_LIMIT), ("account", email.strip().lower(), ACCOUNT_LIMIT)):
            key = hmac.new(service.token_secret, f"password-login:{kind}:{value}".encode(), hashlib.sha256).hexdigest()
            row = service._fetchone(conn, """
                INSERT INTO password_login_budgets(budget_key, window_started_at, attempts) VALUES (?, ?, 1)
                ON CONFLICT(budget_key) DO UPDATE SET
                    attempts = CASE WHEN password_login_budgets.window_started_at < excluded.window_started_at THEN 1
                        WHEN password_login_budgets.attempts < 10000 THEN password_login_budgets.attempts + 1
                        ELSE password_login_budgets.attempts END,
                    window_started_at = CASE WHEN password_login_budgets.window_started_at < excluded.window_started_at
                        THEN excluded.window_started_at ELSE password_login_budgets.window_started_at END
                RETURNING attempts
                """, (key, start))
            if int(row["attempts"]) > limit:
                rejected = True
                break
        conn.commit()
    if rejected:
        raise LoginRateLimitedError(max(1, start + WINDOW_SECONDS - now))
