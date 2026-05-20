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


def apply_sqlite_legacy_schema_bootstrap(service: AccessControlService, conn) -> None:
    """Legacy SQLite bootstrap kept isolated while Postgres moved to migration-first startup."""
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

        CREATE TABLE IF NOT EXISTS anonymous_conversion_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            anonymous_fingerprint TEXT NOT NULL,
            filename TEXT NOT NULL,
            model TEXT NOT NULL,
            conversion_type TEXT NOT NULL,
            status TEXT NOT NULL,
            transactions_count INTEGER,
            pages_count INTEGER,
            scanned_likely INTEGER,
            ocr_used INTEGER NOT NULL DEFAULT 0,
            ocr_pages_processed INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            error_code TEXT
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
    service._sync_admin_emails(conn)
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
        CREATE INDEX IF NOT EXISTS idx_anonymous_conversion_events_created_at
        ON anonymous_conversion_events(created_at DESC)
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
        fetchone=service._fetchone,
        execute=service._execute,
        now_iso=service.now_provider().isoformat(),
        true_value=service._true_value(),
    )
