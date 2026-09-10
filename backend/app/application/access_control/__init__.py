from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency for postgres deployments
    psycopg = None
    dict_row = None

try:
    from psycopg_pool import ConnectionPool
except Exception:  # pragma: no cover - optional dependency for postgres deployments
    ConnectionPool = None

from app.application.access_control.access_control_admin import AccessControlAdminComponent
from app.application.access_control.access_control_auth import AccessControlAuthComponent
from app.application.access_control.access_control_checkout import AccessControlCheckoutComponent
from app.application.access_control.access_control_db import AccessControlDbComponent
from app.application.access_control.access_control_email_verification import AccessControlEmailVerificationComponent
from app.application.access_control.access_control_helpers import AccessControlHelpersComponent
from app.application.access_control.access_control_identity import AccessControlIdentityComponent
from app.application.access_control.access_control_login_events import AccessControlLoginEventsComponent
from app.application.access_control.access_control_quota import AccessControlQuotaComponent
from app.application.access_control.access_control_schema import AccessControlSchemaComponent
from app.application.access_control.access_control_session import AccessControlSessionComponent
from app.application.access_control.access_control_session_core import AccessControlSessionCoreComponent
from app.application.conversion.identity import IdentityContext
from app.application.errors import InvalidUserTokenError

ANONYMOUS_QUOTA_LIMIT = 3
REGISTERED_QUOTA_LIMIT = 10
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PAGES_PER_FILE = 10
QUOTA_WINDOW_DAYS = 7
SESSION_ACCESS_TOKEN_TTL_SECONDS = 15 * 60
SESSION_REFRESH_TOKEN_TTL_SECONDS = 14 * 24 * 60 * 60
DEFAULT_ACTIVE_PLAN_CACHE_TTL_SECONDS = 20
DEFAULT_DB_CONNECT_RETRY_ATTEMPTS = 3
DEFAULT_DB_CONNECT_RETRY_BASE_MS = 200
DEFAULT_DB_POOL_MIN_SIZE = 1
DEFAULT_DB_POOL_MAX_SIZE = 3
DEFAULT_DB_POOL_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RegisteredUser:
    user_id: str
    email: str
    name: str
    token: str
    is_admin: bool = False
    is_active: bool = True
    email_verification_status: str = "verified"
    email_verified_at: str | None = None


@dataclass(frozen=True)
class EmailVerificationToken:
    token_id: str
    user_id: str
    email: str
    name: str
    token: str
    expires_at: str


@dataclass(frozen=True)
class SessionTokenBundle:
    user: RegisteredUser
    access_token: str
    refresh_token: str
    access_expires_at: str
    refresh_expires_at: str


class AccessControlService:
    def __init__(
        self,
        state_file: Path,
        token_secret: str,
        database_url: str | None = None,
        database_schema: str | None = None,
        admin_emails: set[str] | None = None,
        anonymous_quota_limit: int = ANONYMOUS_QUOTA_LIMIT,
        registered_quota_limit: int = REGISTERED_QUOTA_LIMIT,
        quota_window_days: int = QUOTA_WINDOW_DAYS,
        session_access_token_ttl_seconds: int = SESSION_ACCESS_TOKEN_TTL_SECONDS,
        session_refresh_token_ttl_seconds: int = SESSION_REFRESH_TOKEN_TTL_SECONDS,
        active_plan_cache_ttl_seconds: int = DEFAULT_ACTIVE_PLAN_CACHE_TTL_SECONDS,
        db_connect_retry_attempts: int = DEFAULT_DB_CONNECT_RETRY_ATTEMPTS,
        db_connect_retry_base_ms: int = DEFAULT_DB_CONNECT_RETRY_BASE_MS,
        db_pool_min_size: int = DEFAULT_DB_POOL_MIN_SIZE,
        db_pool_max_size: int = DEFAULT_DB_POOL_MAX_SIZE,
        db_pool_timeout_seconds: float = DEFAULT_DB_POOL_TIMEOUT_SECONDS,
        email_verification_required: bool = False,
        email_verification_ttl_seconds: int = 3600,
        email_verification_resend_cooldown_seconds: int = 60,
        email_verification_daily_limit: int = 5,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.state_file = state_file
        self.db_file = state_file.with_suffix(".db")
        self.database_url = (database_url or "").strip()
        self.database_schema = self._normalize_database_schema(database_schema)
        self.admin_emails = AccessControlAdminComponent.normalize_admin_emails(admin_emails)
        self._use_postgres = self.database_url.startswith("postgres://") or self.database_url.startswith(
            "postgresql://"
        )
        self.token_secret = token_secret.encode("utf-8")
        self.anonymous_quota_limit = anonymous_quota_limit
        self.registered_quota_limit = registered_quota_limit
        self.quota_window_days = max(1, int(quota_window_days))
        self.session_access_token_ttl_seconds = max(60, int(session_access_token_ttl_seconds))
        self.session_refresh_token_ttl_seconds = max(300, int(session_refresh_token_ttl_seconds))
        self.active_plan_cache_ttl_seconds = max(0, int(active_plan_cache_ttl_seconds))
        self.db_connect_retry_attempts = max(1, int(db_connect_retry_attempts))
        self.db_connect_retry_base_ms = max(50, int(db_connect_retry_base_ms))
        self.db_pool_min_size = max(1, int(db_pool_min_size))
        self.db_pool_max_size = max(self.db_pool_min_size, int(db_pool_max_size))
        self.db_pool_timeout_seconds = max(1.0, float(db_pool_timeout_seconds))
        self.email_verification_required = bool(email_verification_required)
        self.email_verification_ttl_seconds = max(300, int(email_verification_ttl_seconds))
        self.email_verification_resend_cooldown_seconds = max(
            1, int(email_verification_resend_cooldown_seconds)
        )
        self.email_verification_daily_limit = max(1, int(email_verification_daily_limit))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._identity_context_factory = IdentityContext
        self._registered_user_factory = RegisteredUser
        self._email_verification_token_factory = EmailVerificationToken
        self._session_token_bundle_factory = SessionTokenBundle
        self._active_plan_cache: dict[str, tuple[float, dict[str, str | int] | None]] = {}
        self._postgres_pool = None
        self._postgres_module = psycopg
        self._postgres_dict_row = dict_row
        if not self._use_postgres:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
        elif psycopg is None:
            raise RuntimeError("PostgreSQL support requires psycopg. Install backend requirements.")
        elif ConnectionPool is not None:
            self._postgres_pool = ConnectionPool(
                conninfo=self.database_url,
                kwargs={"row_factory": dict_row},
                min_size=self.db_pool_min_size,
                max_size=self.db_pool_max_size,
                timeout=self.db_pool_timeout_seconds,
                open=True,
            )
        self.db = AccessControlDbComponent(self)
        self.helpers = AccessControlHelpersComponent(self)
        self.auth = AccessControlAuthComponent(self)
        self.email_verification = AccessControlEmailVerificationComponent(self)
        self.schema = AccessControlSchemaComponent(self)
        self.session_core = AccessControlSessionCoreComponent(self)
        self.identity = AccessControlIdentityComponent(self)
        self.session = AccessControlSessionComponent(self)
        self.quota = AccessControlQuotaComponent(self)
        self.admin = AccessControlAdminComponent(self)
        self.login_events = AccessControlLoginEventsComponent(self)
        self.checkout = AccessControlCheckoutComponent(self)
        self._init_db()

    def close(self) -> None:
        pool = self._postgres_pool
        if pool is not None:
            pool.close()
            self._postgres_pool = None

    def resolve_identity(
        self,
        anonymous_fingerprint: str | None,
        user_token: str | None,
    ) -> IdentityContext:
        return self.identity.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
        )

    def issue_anonymous_identity_token(
        self,
        *,
        fingerprint: str | None = None,
        identity_id: str | None = None,
    ) -> str:
        has_fingerprint = bool(str(fingerprint or "").strip())
        has_identity_id = bool(str(identity_id or "").strip())
        if has_fingerprint == has_identity_id:
            raise InvalidUserTokenError
        if has_identity_id:
            return self.helpers.encode_anonymous_identity_token(
                subject_kind="i",
                subject_value=str(identity_id),
            )
        return self.helpers.encode_anonymous_identity_token(
            subject_kind="f",
            subject_value=str(fingerprint),
        )

    def decode_anonymous_identity_token(self, *, token: str) -> str:
        subject_kind, subject_value = self.helpers.decode_anonymous_identity_token(token)
        if subject_kind == "f":
            return subject_value
        fingerprint = self.identity.get_anonymous_fingerprint(subject_value)
        if fingerprint is None:
            raise InvalidUserTokenError
        return fingerprint

    def anonymous_identity_exists(self, *, fingerprint: str) -> bool:
        return self.identity.anonymous_identity_exists(fingerprint)

    def get_anonymous_identity_id(self, *, fingerprint: str) -> str | None:
        return self.identity.get_anonymous_identity_id(fingerprint)

    def generate_anonymous_fingerprint(self) -> str:
        return self.identity.generate_anonymous_fingerprint()

    def register_user(
        self,
        name: str,
        email: str,
        password: str,
        *,
        terms_accepted_at: str | None = None,
        privacy_accepted_at: str | None = None,
        product_updates_opt_in: bool = False,
        product_updates_opted_in_at: str | None = None,
    ) -> RegisteredUser:
        return self.auth.register_user(
            name=name,
            email=email,
            password=password,
            terms_accepted_at=terms_accepted_at,
            privacy_accepted_at=privacy_accepted_at,
            product_updates_opt_in=product_updates_opt_in,
            product_updates_opted_in_at=product_updates_opted_in_at,
        )

    def authenticate_user(self, email: str, password: str) -> RegisteredUser:
        return self.auth.authenticate_user(email=email, password=password)

    def issue_email_verification_token(
        self,
        *,
        user_id: str,
        enforce_resend_limits: bool = False,
    ) -> EmailVerificationToken:
        return self.email_verification.issue_token(
            user_id=user_id,
            enforce_resend_limits=enforce_resend_limits,
        )

    def issue_email_verification_token_for_email(self, *, email: str) -> EmailVerificationToken | None:
        return self.email_verification.issue_token_for_email(email=email)

    def mark_email_verification_delivery(
        self,
        *,
        token_id: str,
        status: str,
        provider_message_id: str | None = None,
    ) -> None:
        self.email_verification.mark_delivery(
            token_id=token_id,
            status=status,
            provider_message_id=provider_message_id,
        )

    def confirm_email_verification_token(self, *, token: str) -> RegisteredUser:
        return self.email_verification.confirm_token(token=token)

    def record_successful_login(self, *, user_id: str, auth_method: str) -> str:
        return self.login_events.record_successful_login(
            user_id=user_id,
            auth_method=auth_method,
        )

    def list_user_login_events_for_admin(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, str]]:
        return self.login_events.list_user_login_events_for_admin(
            user_id=user_id,
            limit=limit,
        )

    def get_user_by_token(self, user_token: str) -> RegisteredUser:
        return self.auth.get_user_by_token(user_token)

    def create_user_session(
        self,
        *,
        user_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionTokenBundle:
        return self.session.create_user_session(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def refresh_user_session(
        self,
        *,
        refresh_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionTokenBundle:
        return self.session.refresh_user_session(
            refresh_token=refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def revoke_user_session(self, *, refresh_token: str) -> None:
        self.session.revoke_user_session(refresh_token=refresh_token)

    def revoke_all_user_sessions(self, *, user_id: str) -> None:
        self.session.revoke_all_user_sessions(user_id=user_id)

    def get_user_by_session_access_token(self, access_token: str) -> RegisteredUser:
        return self.session.get_user_by_session_access_token(access_token)

    def get_user_by_email(self, email: str) -> RegisteredUser:
        return self.auth.get_user_by_email(email)

    def get_user_by_id(self, user_id: str) -> RegisteredUser:
        return self.session_core.get_registered_user_by_id(user_id=user_id)

    def is_user_admin(self, *, user_id: str) -> bool:
        return self.admin.is_user_admin(user_id=user_id)

    def register_or_authenticate_google_user(
        self,
        *,
        provider_user_id: str,
        email: str,
        name: str,
        allow_create: bool = True,
        terms_accepted_at: str | None = None,
        privacy_accepted_at: str | None = None,
        product_updates_opt_in: bool = False,
        product_updates_opted_in_at: str | None = None,
    ) -> RegisteredUser:
        return self.auth.register_or_authenticate_google_user(
            provider_user_id=provider_user_id,
            email=email,
            name=name,
            allow_create=allow_create,
            terms_accepted_at=terms_accepted_at,
            privacy_accepted_at=privacy_accepted_at,
            product_updates_opt_in=product_updates_opt_in,
            product_updates_opted_in_at=product_updates_opted_in_at,
        )

    def create_google_oauth_state(
        self,
        *,
        next_path: str,
        ttl_seconds: int = 600,
        flow_mode: str = "login",
        terms_accepted: bool = False,
        product_updates_opt_in: bool = False,
    ) -> tuple[str, str]:
        return self.identity.create_google_oauth_state(
            next_path=next_path,
            ttl_seconds=ttl_seconds,
            flow_mode=flow_mode,
            terms_accepted=terms_accepted,
            product_updates_opt_in=product_updates_opt_in,
        )

    def consume_google_oauth_state(self, *, state: str) -> dict[str, str] | None:
        return self.identity.consume_google_oauth_state(state=state)

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int = MAX_UPLOAD_SIZE_BYTES) -> None:
        self.quota.assert_upload_size(raw_bytes, max_upload_size_bytes)

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None:
        self.quota.ensure_quota_available(identity, required_units=required_units)

    def consume_quota(
        self,
        identity: IdentityContext,
        *,
        consumed_units: int = 1,
        idempotency_key: str | None = None,
    ) -> int:
        return self.quota.consume_quota(
            identity,
            consumed_units=consumed_units,
            idempotency_key=idempotency_key,
        )

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        return self.quota.get_remaining_quota(identity)

    def get_quota_reset_at(self, identity: IdentityContext) -> str:
        return self.quota.get_quota_reset_at(identity)

    def record_user_conversion(
        self,
        *,
        user_id: str,
        processing_id: str,
        filename: str,
        model: str,
        conversion_type: str,
        status: str,
        transactions_count: int | None,
        pages_count: int | None = None,
        scanned_likely: bool | None = None,
        ocr_used: bool = False,
        ocr_pages_processed: int = 0,
        duration_ms: int = 0,
        error_code: str | None = None,
        error_stage: str | None = None,
        error_subcode: str | None = None,
        exception_class: str | None = None,
        layout_inference_name: str | None = None,
        layout_inference_confidence: float | None = None,
        selected_parser: str | None = None,
        parser_selection_reason: str | None = None,
        pdf_page_count: int | None = None,
        extracted_char_count: int | None = None,
        ocr_attempted: bool = False,
        ocr_engine: str | None = None,
        file_sha256: str | None = None,
        canonical_warning_transactions_count: int = 0,
        balance_consistency_failed: int = 0,
        parser_confidence_band: str | None = None,
        parser_coverage_rate: float | None = None,
        warning_types: list[str] | None = None,
        failure_diagnostics: dict[str, object] | None = None,
        quality_issues: list[dict[str, object]] | None = None,
        created_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        self.checkout.record_user_conversion(
            user_id=user_id,
            processing_id=processing_id,
            filename=filename,
            model=model,
            conversion_type=conversion_type,
            status=status,
            transactions_count=transactions_count,
            pages_count=pages_count,
            scanned_likely=scanned_likely,
            ocr_used=ocr_used,
            ocr_pages_processed=ocr_pages_processed,
            duration_ms=duration_ms,
            error_code=error_code,
            error_stage=error_stage,
            error_subcode=error_subcode,
            exception_class=exception_class,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
            selected_parser=selected_parser,
            parser_selection_reason=parser_selection_reason,
            pdf_page_count=pdf_page_count,
            extracted_char_count=extracted_char_count,
            ocr_attempted=ocr_attempted,
            ocr_engine=ocr_engine,
            file_sha256=file_sha256,
            canonical_warning_transactions_count=canonical_warning_transactions_count,
            balance_consistency_failed=balance_consistency_failed,
            parser_confidence_band=parser_confidence_band,
            parser_coverage_rate=parser_coverage_rate,
            warning_types=warning_types,
            failure_diagnostics=failure_diagnostics,
            quality_issues=quality_issues,
            created_at=created_at,
            expires_at=expires_at,
        )

    def list_user_conversions(self, *, user_id: str, limit: int = 20) -> list[dict[str, str | int]]:
        return self.checkout.list_user_conversions(user_id=user_id, limit=limit)

    def record_anonymous_conversion_event(
        self,
        *,
        event_id: str,
        anonymous_fingerprint: str,
        filename: str,
        model: str,
        conversion_type: str,
        status: str,
        transactions_count: int | None,
        pages_count: int | None,
        scanned_likely: bool | None,
        ocr_used: bool,
        ocr_pages_processed: int,
        duration_ms: int,
        canonical_warning_transactions_count: int = 0,
        balance_consistency_failed: int = 0,
        error_code: str | None = None,
        error_stage: str | None = None,
        error_subcode: str | None = None,
        exception_class: str | None = None,
        layout_inference_name: str | None = None,
        layout_inference_confidence: float | None = None,
        selected_parser: str | None = None,
        parser_selection_reason: str | None = None,
        pdf_page_count: int | None = None,
        extracted_char_count: int | None = None,
        ocr_attempted: bool = False,
        ocr_engine: str | None = None,
        file_sha256: str | None = None,
        parser_confidence_band: str | None = None,
        parser_coverage_rate: float | None = None,
        warning_types: list[str] | None = None,
        failure_diagnostics: dict[str, object] | None = None,
        quality_issues: list[dict[str, object]] | None = None,
    ) -> None:
        self.checkout.record_anonymous_conversion_event(
            event_id=event_id,
            anonymous_fingerprint=anonymous_fingerprint,
            filename=filename,
            model=model,
            conversion_type=conversion_type,
            status=status,
            transactions_count=transactions_count,
            pages_count=pages_count,
            scanned_likely=scanned_likely,
            ocr_used=ocr_used,
            ocr_pages_processed=ocr_pages_processed,
            duration_ms=duration_ms,
            canonical_warning_transactions_count=canonical_warning_transactions_count,
            balance_consistency_failed=balance_consistency_failed,
            error_code=error_code,
            error_stage=error_stage,
            error_subcode=error_subcode,
            exception_class=exception_class,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
            selected_parser=selected_parser,
            parser_selection_reason=parser_selection_reason,
            pdf_page_count=pdf_page_count,
            extracted_char_count=extracted_char_count,
            ocr_attempted=ocr_attempted,
            ocr_engine=ocr_engine,
            file_sha256=file_sha256,
            parser_confidence_band=parser_confidence_band,
            parser_coverage_rate=parser_coverage_rate,
            warning_types=warning_types,
            failure_diagnostics=failure_diagnostics,
            quality_issues=quality_issues,
        )

    def list_public_plans(self) -> list[dict[str, str | int]]:
        return self.checkout.list_public_plans()

    def activate_user_plan(
        self,
        *,
        user_id: str,
        plan_code: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int]:
        return self.checkout.activate_user_plan(
            user_id=user_id,
            plan_code=plan_code,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )

    def create_checkout_intent(
        self,
        *,
        user_id: str,
        plan_code: str,
        customer_name: str,
        customer_email: str,
        customer_whatsapp: str,
        customer_document: str | None = None,
        customer_notes: str | None = None,
    ) -> dict[str, str | int]:
        return self.checkout.create_checkout_intent(
            user_id=user_id,
            plan_code=plan_code,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_whatsapp=customer_whatsapp,
            customer_document=customer_document,
            customer_notes=customer_notes,
        )

    def read_checkout_intent_for_user(
        self,
        *,
        intent_id: str,
        user_id: str,
        customer_email: str | None = None,
    ) -> dict[str, str | int | None] | None:
        return self.checkout.read_checkout_intent_for_user(
            intent_id=intent_id,
            user_id=user_id,
            customer_email=customer_email,
        )

    def read_latest_checkout_intent_for_user(
        self,
        *,
        user_id: str,
        customer_email: str | None = None,
    ) -> dict[str, str | int | None] | None:
        return self.checkout.read_latest_checkout_intent_for_user(
            user_id=user_id,
            customer_email=customer_email,
        )

    def mark_checkout_intent_awaiting_payment(
        self,
        *,
        intent_id: str,
        payment_link: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int | None]:
        return self.checkout.mark_checkout_intent_awaiting_payment(
            intent_id=intent_id,
            payment_link=payment_link,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )

    def read_checkout_intent_by_id(self, *, intent_id: str) -> dict[str, str | int | None] | None:
        return self.checkout.read_checkout_intent_by_id(intent_id=intent_id)

    def mark_checkout_intent_released_by_id(
        self,
        *,
        intent_id: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int | None]:
        return self.checkout.mark_checkout_intent_released_by_id(
            intent_id=intent_id,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )

    def list_checkout_intents_for_admin(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | int | None]], int]:
        return self.checkout.list_checkout_intents_for_admin(
            statuses=statuses,
            query=query,
            limit=limit,
            offset=offset,
        )

    def list_checkout_intent_events_for_admin(
        self,
        *,
        intent_id: str,
        limit: int = 100,
    ) -> list[dict[str, str | None]]:
        return self.checkout.list_checkout_intent_events_for_admin(
            intent_id=intent_id,
            limit=limit,
        )

    def list_users_for_admin(
        self,
        *,
        query: str | None = None,
        only_admin: bool | None = None,
        only_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | bool | int | None]], int]:
        return self.admin.list_users_for_admin(
            query=query,
            only_admin=only_admin,
            only_active=only_active,
            limit=limit,
            offset=offset,
        )

    def set_user_admin_role(self, *, user_id: str, is_admin: bool) -> dict[str, str | bool]:
        return self.admin.set_user_admin_role(user_id=user_id, is_admin=is_admin)

    def set_user_admin_role_with_actor(
        self,
        *,
        user_id: str,
        is_admin: bool,
        actor_user_id: str | None,
    ) -> dict[str, str | bool]:
        return self.admin.set_user_admin_role_with_actor(
            user_id=user_id,
            is_admin=is_admin,
            actor_user_id=actor_user_id,
        )

    def set_user_active_status(self, *, user_id: str, is_active: bool) -> dict[str, str | bool]:
        return self.admin.set_user_active_status(user_id=user_id, is_active=is_active)

    def set_user_active_status_with_actor(
        self,
        *,
        user_id: str,
        is_active: bool,
        actor_user_id: str | None,
    ) -> dict[str, str | bool]:
        return self.admin.set_user_active_status_with_actor(
            user_id=user_id,
            is_active=is_active,
            actor_user_id=actor_user_id,
        )

    def list_user_role_events_for_admin(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, str | bool | None]]:
        return self.admin.list_user_role_events_for_admin(user_id=user_id, limit=limit)

    def _ensure_anonymous_identity(self, fingerprint: str) -> str:
        return self.identity.ensure_anonymous_identity(fingerprint)

    def _read_active_user_plan(self, *, user_id: str) -> dict[str, str | int] | None:
        return self.helpers.read_active_user_plan(user_id=user_id)

    def _read_usage(self, identity: IdentityContext) -> dict[str, int | datetime]:
        return self.helpers.read_usage(identity)

    def _invalidate_active_plan_cache(self, user_id: str) -> None:
        self.helpers.invalidate_active_plan_cache(user_id)

    def _hash_password(self, password: str, salt: str) -> str:
        return self.helpers.hash_password(password=password, salt=salt)

    def _verify_password(self, password: str, stored_hash: str, stored_salt: str) -> bool:
        return self.helpers.verify_password(password=password, stored_hash=stored_hash, stored_salt=stored_salt)

    def _create_user_session_bundle(
        self,
        *,
        user: RegisteredUser,
        ip_address: str | None,
        user_agent: str | None,
    ) -> SessionTokenBundle:
        return self.session_core.create_user_session_bundle(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _create_user_session_bundle_with_conn(
        self,
        *,
        conn,
        user: RegisteredUser,
        family_id: str,
        ip_address: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> SessionTokenBundle:
        return self.session_core.create_user_session_bundle_with_conn(
            conn=conn,
            user=user,
            family_id=family_id,
            ip_address=ip_address,
            user_agent=user_agent,
            now=now,
        )

    def _revoke_session_family_with_conn(self, conn, *, family_id: str, reason: str, now_iso: str) -> None:
        self.session_core.revoke_session_family_with_conn(
            conn,
            family_id=family_id,
            reason=reason,
            now_iso=now_iso,
        )

    def _get_registered_user_by_id(self, *, user_id: str) -> RegisteredUser:
        return self.session_core.get_registered_user_by_id(user_id=user_id)

    def _get_registered_user_by_id_with_conn(self, *, conn, user_id: str) -> RegisteredUser:
        return self.session_core.get_registered_user_by_id_with_conn(conn=conn, user_id=user_id)

    def _hash_refresh_token(self, refresh_token: str) -> str:
        return self.session_core.hash_refresh_token(refresh_token)

    def _encode_session_token(self, *, user_id: str, session_id: str, token_type: str, expires_at: datetime) -> str:
        return self.session_core.encode_session_token(
            user_id=user_id,
            session_id=session_id,
            token_type=token_type,
            expires_at=expires_at,
        )

    def _decode_session_token(self, token: str, *, expected_type: str) -> dict[str, str | int]:
        return self.session_core.decode_session_token(token, expected_type=expected_type)

    def _extract_session_id_from_token(self, refresh_token: str) -> str:
        return self.session_core.extract_session_id_from_token(refresh_token)

    def _encode_token(self, user_id: str) -> str:
        return self.helpers.encode_token(user_id)

    def _decode_token(self, token: str) -> str:
        return self.helpers.decode_token(token)

    def _user_exists(self, user_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(
                    conn,
                    "SELECT id, is_active, email_verification_status FROM users WHERE id = ?",
                    (user_id,),
                )
                return (
                    row is not None
                    and self._row_is_active(row)
                    and self.email_verification.row_is_verified(row)
                )

    def _connect(self):
        return self.db.connect()

    def _is_retryable_db_exception(self, exc: Exception) -> bool:
        return self.db.is_retryable_db_exception(exc)

    def _init_db(self) -> None:
        self.schema.init_db()

    def _is_quota_window_expired(self, window_started_at: datetime, now: datetime, *, quota_window_days: int) -> bool:
        return self.helpers.is_quota_window_expired(
            window_started_at=window_started_at,
            now=now,
            quota_window_days=quota_window_days,
        )

    def _parse_usage_datetime(self, raw_value: str, fallback: datetime) -> datetime:
        return self.helpers.parse_usage_datetime(raw_value, fallback)

    def _normalize_next_path(self, next_path: str | None) -> str:
        return self.identity.normalize_next_path(next_path)

    def _adapt_query(self, query: str) -> str:
        return self.db.adapt_query(query)

    def _true_value(self):
        return self.db.true_value()

    def _false_value(self):
        return self.db.false_value()

    def _execute(self, conn, query: str, params: tuple = ()):
        return self.db.execute(conn, query, params)

    def _fetchone(self, conn, query: str, params: tuple = ()):
        return self.db.fetchone(conn, query, params)

    def _fetchall(self, conn, query: str, params: tuple = ()):
        return self.db.fetchall(conn, query, params)

    def _append_checkout_intent_event_with_conn(
        self,
        conn,
        *,
        intent_id: str,
        event_type: str,
        event_message: str,
        actor_kind: str,
        actor_user_id: str | None,
        payload: dict[str, str] | None,
        created_at: str,
    ) -> None:
        self.helpers.append_checkout_intent_event_with_conn(
            conn,
            intent_id=intent_id,
            event_type=event_type,
            event_message=event_message,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            payload=payload,
            created_at=created_at,
        )

    def _sync_admin_emails(self, conn) -> None:
        self.admin.sync_admin_emails(conn)

    def _row_is_admin(self, row) -> bool:
        return self.admin.row_is_admin(row)

    def _row_is_active(self, row) -> bool:
        return self.admin.row_is_active(row)

    def _row_bool_from_value(self, raw) -> bool:
        return self.admin.row_bool_from_value(raw)

    def _normalize_database_schema(self, schema: str | None) -> str:
        return AccessControlHelpersComponent.normalize_database_schema(schema)

