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
            terms_accepted_at TEXT,
            privacy_accepted_at TEXT,
            product_updates_opt_in INTEGER NOT NULL DEFAULT 0,
            product_updates_opted_in_at TEXT,
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
            scanned_likely INTEGER,
            ocr_used INTEGER NOT NULL DEFAULT 0,
            ocr_pages_processed INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_stage TEXT,
            error_subcode TEXT,
            exception_class TEXT,
            layout_inference_name TEXT,
            layout_inference_confidence REAL,
            selected_parser TEXT,
            parser_selection_reason TEXT,
            pdf_page_count INTEGER,
            extracted_char_count INTEGER,
            ocr_attempted INTEGER NOT NULL DEFAULT 0,
            ocr_engine TEXT,
            file_sha256 TEXT,
            canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0,
            balance_consistency_failed INTEGER NOT NULL DEFAULT 0,
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
            canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0,
            balance_consistency_failed INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_stage TEXT,
            error_subcode TEXT,
            exception_class TEXT,
            layout_inference_name TEXT,
            layout_inference_confidence REAL,
            selected_parser TEXT,
            parser_selection_reason TEXT,
            pdf_page_count INTEGER,
            extracted_char_count INTEGER,
            ocr_attempted INTEGER NOT NULL DEFAULT 0,
            ocr_engine TEXT,
            file_sha256 TEXT
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
            max_pages_per_file_ocr INTEGER NOT NULL DEFAULT 6,
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
    if "terms_accepted_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")
    if "privacy_accepted_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN privacy_accepted_at TEXT")
    if "product_updates_opt_in" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN product_updates_opt_in INTEGER NOT NULL DEFAULT 0")
    if "product_updates_opted_in_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN product_updates_opted_in_at TEXT")
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
    if "scanned_likely" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN scanned_likely INTEGER")
    if "ocr_used" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN ocr_used INTEGER NOT NULL DEFAULT 0")
    if "ocr_pages_processed" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN ocr_pages_processed INTEGER NOT NULL DEFAULT 0")
    if "duration_ms" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0")
    if "error_code" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN error_code TEXT")
    if "error_stage" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN error_stage TEXT")
    if "error_subcode" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN error_subcode TEXT")
    if "exception_class" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN exception_class TEXT")
    if "layout_inference_name" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN layout_inference_name TEXT")
    if "layout_inference_confidence" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN layout_inference_confidence REAL")
    if "selected_parser" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN selected_parser TEXT")
    if "parser_selection_reason" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN parser_selection_reason TEXT")
    if "pdf_page_count" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN pdf_page_count INTEGER")
    if "extracted_char_count" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN extracted_char_count INTEGER")
    if "ocr_attempted" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN ocr_attempted INTEGER NOT NULL DEFAULT 0")
    if "ocr_engine" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN ocr_engine TEXT")
    if "file_sha256" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN file_sha256 TEXT")
    if "canonical_warning_transactions_count" not in user_conversions_columns:
        conn.execute(
            "ALTER TABLE user_conversions ADD COLUMN canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0"
        )
    if "balance_consistency_failed" not in user_conversions_columns:
        conn.execute("ALTER TABLE user_conversions ADD COLUMN balance_consistency_failed INTEGER NOT NULL DEFAULT 0")
    anonymous_conversion_event_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(anonymous_conversion_events)").fetchall()
    }
    if "canonical_warning_transactions_count" not in anonymous_conversion_event_columns:
        conn.execute(
            "ALTER TABLE anonymous_conversion_events ADD COLUMN canonical_warning_transactions_count INTEGER NOT NULL DEFAULT 0"
        )
    if "balance_consistency_failed" not in anonymous_conversion_event_columns:
        conn.execute(
            "ALTER TABLE anonymous_conversion_events ADD COLUMN balance_consistency_failed INTEGER NOT NULL DEFAULT 0"
        )
    if "error_stage" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN error_stage TEXT")
    if "error_subcode" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN error_subcode TEXT")
    if "exception_class" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN exception_class TEXT")
    if "layout_inference_name" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN layout_inference_name TEXT")
    if "layout_inference_confidence" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN layout_inference_confidence REAL")
    if "selected_parser" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN selected_parser TEXT")
    if "parser_selection_reason" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN parser_selection_reason TEXT")
    if "pdf_page_count" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN pdf_page_count INTEGER")
    if "extracted_char_count" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN extracted_char_count INTEGER")
    if "ocr_attempted" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN ocr_attempted INTEGER NOT NULL DEFAULT 0")
    if "ocr_engine" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN ocr_engine TEXT")
    if "file_sha256" not in anonymous_conversion_event_columns:
        conn.execute("ALTER TABLE anonymous_conversion_events ADD COLUMN file_sha256 TEXT")
    plan_versions_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(plan_versions)").fetchall()
    }
    if "max_pages_per_file_ocr" not in plan_versions_columns:
        conn.execute("ALTER TABLE plan_versions ADD COLUMN max_pages_per_file_ocr INTEGER NOT NULL DEFAULT 6")
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
