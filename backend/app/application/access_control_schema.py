from __future__ import annotations

from typing import TYPE_CHECKING

from app.application.checkout_management import (
    CHECKOUT_STATUS_PENDING_LEGACY,
    CHECKOUT_STATUS_REQUESTED,
)
from app.application.plan_management import (
    seed_default_public_plans,
)

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class AccessControlSchemaComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def init_db(self) -> None:
        if self._service._use_postgres:
            self.init_postgres_db()
            return

        with self._service._lock:
            with self._service._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE,
                        is_admin INTEGER NOT NULL DEFAULT 0,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
                        auth_provider TEXT NOT NULL DEFAULT 'local',
                        provider_user_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS anonymous_identities (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS usage (
                        identity_type TEXT NOT NULL,
                        identity_id TEXT NOT NULL,
                        used_count INTEGER NOT NULL,
                        quota_limit INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        window_started_at TEXT,
                        PRIMARY KEY (identity_type, identity_id)
                    );

                    CREATE TABLE IF NOT EXISTS user_conversions (
                        analysis_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        filename TEXT NOT NULL,
                        model TEXT NOT NULL,
                        conversion_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        transactions_count INTEGER NOT NULL DEFAULT 0,
                        pages_count INTEGER,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    );

                    CREATE TABLE IF NOT EXISTS google_oauth_states (
                        state TEXT PRIMARY KEY,
                        code_verifier TEXT NOT NULL,
                        next_path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        refresh_token_hash TEXT NOT NULL UNIQUE,
                        refresh_token_family TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        rotated_at TEXT,
                        revoked_at TEXT,
                        replaced_by_session_id TEXT,
                        revoke_reason TEXT,
                        last_ip TEXT,
                        last_user_agent TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    );

                    CREATE TABLE IF NOT EXISTS plan_versions (
                        id TEXT PRIMARY KEY,
                        code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        currency TEXT NOT NULL,
                        price_cents INTEGER NOT NULL,
                        billing_period TEXT NOT NULL,
                        quota_mode TEXT NOT NULL,
                        quota_limit INTEGER NOT NULL,
                        quota_window_days INTEGER NOT NULL,
                        max_upload_size_bytes INTEGER NOT NULL,
                        max_pages_per_file INTEGER NOT NULL,
                        is_public INTEGER NOT NULL DEFAULT 1,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_plan_subscriptions (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        plan_version_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id),
                        FOREIGN KEY(plan_version_id) REFERENCES plan_versions(id)
                    );

                    CREATE TABLE IF NOT EXISTS checkout_intents (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        user_id TEXT,
                        plan_code TEXT NOT NULL,
                        plan_name TEXT NOT NULL,
                        price_cents INTEGER NOT NULL,
                        currency TEXT NOT NULL,
                        billing_period TEXT NOT NULL,
                        customer_name TEXT NOT NULL,
                        customer_email TEXT NOT NULL,
                        customer_whatsapp TEXT NOT NULL,
                        customer_document TEXT,
                        customer_notes TEXT,
                        payment_link TEXT,
                        payment_link_sent_at TEXT,
                        released_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS checkout_intent_events (
                        id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        event_message TEXT,
                        actor_kind TEXT NOT NULL,
                        actor_user_id TEXT,
                        payload_json TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(intent_id) REFERENCES checkout_intents(id),
                        FOREIGN KEY(actor_user_id) REFERENCES users(id)
                    );

                    CREATE TABLE IF NOT EXISTS admin_user_role_events (
                        id TEXT PRIMARY KEY,
                        target_user_id TEXT NOT NULL,
                        target_email TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        actor_user_id TEXT,
                        actor_email TEXT,
                        previous_is_admin INTEGER NOT NULL,
                        new_is_admin INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(target_user_id) REFERENCES users(id),
                        FOREIGN KEY(actor_user_id) REFERENCES users(id)
                    );
                    """
                )

                user_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                if "auth_provider" not in user_columns:
                    conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
                if "provider_user_id" not in user_columns:
                    conn.execute("ALTER TABLE users ADD COLUMN provider_user_id TEXT")
                if "is_admin" not in user_columns:
                    conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
                conn.execute(
                    """
                    UPDATE users
                    SET auth_provider = 'local'
                    WHERE auth_provider IS NULL OR auth_provider = ''
                    """
                )
                self._service._sync_admin_emails(conn)
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_provider_user_id
                    ON users(provider_user_id)
                    WHERE auth_provider = 'google' AND provider_user_id IS NOT NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_conversions_user_created_at
                    ON user_conversions(user_id, created_at DESC)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_sessions_user_created_at
                    ON user_sessions(user_id, created_at DESC)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_sessions_family
                    ON user_sessions(refresh_token_family)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
                    ON user_sessions(expires_at)
                    """
                )

                usage_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(usage)").fetchall()
                }
                if "window_started_at" not in usage_columns:
                    conn.execute("ALTER TABLE usage ADD COLUMN window_started_at TEXT")
                conn.execute(
                    """
                    UPDATE usage
                    SET window_started_at = updated_at
                    WHERE window_started_at IS NULL OR window_started_at = ''
                    """
                )
                user_conversions_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(user_conversions)").fetchall()
                }
                if "pages_count" not in user_conversions_columns:
                    conn.execute("ALTER TABLE user_conversions ADD COLUMN pages_count INTEGER")
                checkout_intents_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(checkout_intents)").fetchall()
                }
                if "user_id" not in checkout_intents_columns:
                    conn.execute("ALTER TABLE checkout_intents ADD COLUMN user_id TEXT")
                if "payment_link" not in checkout_intents_columns:
                    conn.execute("ALTER TABLE checkout_intents ADD COLUMN payment_link TEXT")
                if "payment_link_sent_at" not in checkout_intents_columns:
                    conn.execute("ALTER TABLE checkout_intents ADD COLUMN payment_link_sent_at TEXT")
                if "released_at" not in checkout_intents_columns:
                    conn.execute("ALTER TABLE checkout_intents ADD COLUMN released_at TEXT")
                conn.execute(
                    """
                    UPDATE checkout_intents
                    SET status = ?
                    WHERE status = ?
                    """,
                    (CHECKOUT_STATUS_REQUESTED, CHECKOUT_STATUS_PENDING_LEGACY),
                )
                seed_default_public_plans(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    now_iso=self._service.now_provider().isoformat(),
                    true_value=self._service._true_value(),
                )
                conn.commit()

    def init_postgres_db(self) -> None:
        with self._service._lock:
            with self._service._connect() as conn:
                self._assert_postgres_schema_ready(conn)

    def _assert_postgres_schema_ready(self, conn) -> None:
        required_tables = (
            "alembic_version",
            "users",
            "anonymous_identities",
            "usage",
            "user_conversions",
            "google_oauth_states",
            "user_sessions",
            "plan_versions",
            "user_plan_subscriptions",
            "checkout_intents",
            "checkout_intent_events",
            "admin_user_role_events",
        )
        missing_tables = [table for table in required_tables if not self._postgres_table_exists(conn, table)]

        required_columns: dict[str, tuple[str, ...]] = {
            "users": ("auth_provider", "provider_user_id", "is_admin"),
            "usage": ("window_started_at",),
            "user_conversions": ("pages_count",),
            "checkout_intents": ("user_id", "payment_link", "payment_link_sent_at", "released_at"),
        }
        missing_columns: list[str] = []
        for table_name, columns in required_columns.items():
            for column_name in columns:
                if not self._postgres_column_exists(conn, table_name, column_name):
                    missing_columns.append(f"{table_name}.{column_name}")

        if not missing_tables and not missing_columns:
            return

        problems: list[str] = []
        if missing_tables:
            problems.append("missing tables: " + ", ".join(sorted(missing_tables)))
        if missing_columns:
            problems.append("missing columns: " + ", ".join(sorted(missing_columns)))

        raise RuntimeError(
            "PostgreSQL schema is not ready for runtime. "
            "Run Alembic migrations before starting the app (`alembic upgrade head`). "
            "For legacy bootstrap databases without version history, run "
            "`alembic stamp 20260508_01` then `alembic upgrade head`. "
            + "; ".join(problems)
        )

    def _postgres_table_exists(self, conn, table_name: str) -> bool:
        qualified = f"{self._service.database_schema}.{table_name}"
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (qualified,))
            row = cur.fetchone()
        if row is None:
            return False
        if isinstance(row, dict):
            return row.get("to_regclass") is not None
        return row[0] is not None

    def _postgres_column_exists(self, conn, table_name: str, column_name: str) -> bool:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND column_name = %s
                LIMIT 1
                """,
                (self._service.database_schema, table_name, column_name),
            )
            return cur.fetchone() is not None
