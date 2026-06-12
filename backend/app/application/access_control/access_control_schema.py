from __future__ import annotations

from typing import TYPE_CHECKING

from app.application.access_control.access_control_schema_sqlite_legacy import (
    apply_sqlite_legacy_schema_bootstrap,
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
                apply_sqlite_legacy_schema_bootstrap(self._service, conn)
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
            "anonymous_conversion_events",
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
            "users": (
                "auth_provider",
                "provider_user_id",
                "is_admin",
                "terms_accepted_at",
                "privacy_accepted_at",
                "product_updates_opt_in",
                "product_updates_opted_in_at",
            ),
            "usage": ("window_started_at",),
            "user_conversions": (
                "pages_count",
                "scanned_likely",
                "ocr_used",
                "ocr_pages_processed",
                "duration_ms",
                "error_code",
                "error_stage",
                "error_subcode",
                "exception_class",
                "layout_inference_name",
                "layout_inference_confidence",
                "selected_parser",
                "parser_selection_reason",
                "pdf_page_count",
                "extracted_char_count",
                "ocr_attempted",
                "ocr_engine",
                "file_sha256",
                "canonical_warning_transactions_count",
                "balance_consistency_failed",
            ),
            "anonymous_conversion_events": (
                "canonical_warning_transactions_count",
                "balance_consistency_failed",
                "error_stage",
                "error_subcode",
                "exception_class",
                "layout_inference_name",
                "layout_inference_confidence",
                "selected_parser",
                "parser_selection_reason",
                "pdf_page_count",
                "extracted_char_count",
                "ocr_attempted",
                "ocr_engine",
                "file_sha256",
            ),
            "plan_versions": ("max_pages_per_file_ocr",),
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
