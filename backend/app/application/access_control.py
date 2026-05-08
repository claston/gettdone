import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator
from uuid import uuid4

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

from app.application.access_control_admin import AccessControlAdminComponent
from app.application.access_control_checkout import AccessControlCheckoutComponent
from app.application.access_control_db import AccessControlDbComponent
from app.application.access_control_identity import AccessControlIdentityComponent
from app.application.access_control_quota import AccessControlQuotaComponent
from app.application.access_control_session import AccessControlSessionComponent
from app.application.checkout_management import (
    CHECKOUT_STATUS_PENDING_LEGACY,
    CHECKOUT_STATUS_REQUESTED,
)
from app.application.checkout_management import (
    insert_checkout_intent_event as insert_checkout_intent_event_query,
)
from app.application.errors import (
    InvalidCredentialsError,
    InvalidSessionTokenError,
    InvalidUserTokenError,
    UserAlreadyExistsError,
)
from app.application.plan_management import (
    read_active_user_plan as read_active_user_plan_query,
)
from app.application.plan_management import (
    seed_default_public_plans,
)
from app.application.quota_management import (
    read_usage_snapshot,
)

ANONYMOUS_QUOTA_LIMIT = 3
REGISTERED_QUOTA_LIMIT = 10
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024
PASSWORD_HASH_ITERATIONS = 390_000
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
class IdentityContext:
    identity_type: str
    identity_id: str
    quota_limit: int
    quota_mode: str = "conversion"
    quota_window_days: int = QUOTA_WINDOW_DAYS
    max_upload_size_bytes: int = MAX_UPLOAD_SIZE_BYTES
    max_pages_per_file: int = 5
    plan_code: str | None = None
    plan_name: str | None = None


@dataclass(frozen=True)
class RegisteredUser:
    user_id: str
    email: str
    name: str
    token: str
    is_admin: bool = False


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
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.state_file = state_file
        self.db_file = state_file.with_suffix(".db")
        self.database_url = (database_url or "").strip()
        self.database_schema = self._normalize_database_schema(database_schema)
        self.admin_emails = self._normalize_admin_emails(admin_emails)
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
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._identity_context_factory = IdentityContext
        self._active_plan_cache: dict[str, tuple[float, dict[str, str | int] | None]] = {}
        self._postgres_pool = None
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
        self.identity = AccessControlIdentityComponent(self)
        self.session = AccessControlSessionComponent(self)
        self.quota = AccessControlQuotaComponent(self)
        self.admin = AccessControlAdminComponent(self)
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

    def register_user(self, name: str, email: str, password: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        is_admin = normalized_email in self.admin_emails
        now = self.now_provider().isoformat()
        user_id = f"usr_{uuid4().hex[:12]}"
        salt = secrets.token_hex(8)
        password_hash = self._hash_password(password=password, salt=salt)
        with self._lock:
            with self._connect() as conn:
                existing = self._fetchone(conn, "SELECT id FROM users WHERE email = ?", (normalized_email,))
                if existing is not None:
                    raise UserAlreadyExistsError
                self._execute(
                    conn,
                    """
                    INSERT INTO users (
                        id,
                        name,
                        email,
                        is_admin,
                        password_hash,
                        password_salt,
                        auth_provider,
                        provider_user_id,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        name.strip(),
                        normalized_email,
                        is_admin,
                        password_hash,
                        salt,
                        "local",
                        None,
                        now,
                        now,
                    ),
                )
                conn.commit()
        return RegisteredUser(
            user_id=user_id,
            email=normalized_email,
            name=name.strip(),
            token=self._encode_token(user_id),
            is_admin=is_admin,
        )

    def authenticate_user(self, email: str, password: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        with self._lock:
            with self._connect() as conn:
                user = self._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin, password_hash, password_salt FROM users WHERE email = ?",
                    (normalized_email,),
                )
                if user is None:
                    raise InvalidCredentialsError
                if not self._verify_password(
                    password=password,
                    stored_hash=str(user["password_hash"] or ""),
                    stored_salt=str(user["password_salt"] or ""),
                ):
                    raise InvalidCredentialsError
                return RegisteredUser(
                    user_id=str(user["id"]),
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=self._encode_token(str(user["id"])),
                    is_admin=self._row_is_admin(user),
                )

    def get_user_by_token(self, user_token: str) -> RegisteredUser:
        user_id = self._decode_token(user_token)
        with self._lock:
            with self._connect() as conn:
                user = self._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE id = ?",
                    (user_id,),
                )
                if user is None:
                    raise InvalidUserTokenError
                return RegisteredUser(
                    user_id=str(user["id"]),
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=user_token,
                    is_admin=self._row_is_admin(user),
                )

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
        normalized_email = email.strip().lower()
        with self._lock:
            with self._connect() as conn:
                user = self._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE lower(email) = ?",
                    (normalized_email,),
                )
                if user is None:
                    raise InvalidUserTokenError
                user_id = str(user["id"])
                return RegisteredUser(
                    user_id=user_id,
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=self._encode_token(user_id),
                    is_admin=self._row_is_admin(user),
                )

    def is_user_admin(self, *, user_id: str) -> bool:
        return self.admin.is_user_admin(user_id=user_id)

    def register_or_authenticate_google_user(
        self,
        *,
        provider_user_id: str,
        email: str,
        name: str,
    ) -> RegisteredUser:
        normalized_email = email.strip().lower()
        provider_user_id = provider_user_id.strip()
        display_name = name.strip() or normalized_email.split("@", 1)[0]
        now = self.now_provider().isoformat()

        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(
                    conn,
                    """
                    SELECT id, name, email, is_admin
                    FROM users
                    WHERE auth_provider = 'google' AND provider_user_id = ?
                    """,
                    (provider_user_id,),
                )
                if row is not None:
                    user_id = str(row["id"])
                    self._execute(
                        conn,
                        """
                        UPDATE users
                        SET name = ?, email = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (display_name, normalized_email, now, user_id),
                    )
                    conn.commit()
                    return RegisteredUser(
                        user_id=user_id,
                        email=normalized_email,
                        name=display_name,
                        token=self._encode_token(user_id),
                        is_admin=self._row_is_admin(row),
                    )

                existing_by_email = self._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE email = ?",
                    (normalized_email,),
                )
                if existing_by_email is not None:
                    user_id = str(existing_by_email["id"])
                    self._execute(
                        conn,
                        """
                        UPDATE users
                        SET name = ?, auth_provider = 'google', provider_user_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (display_name, provider_user_id, now, user_id),
                    )
                    conn.commit()
                    return RegisteredUser(
                        user_id=user_id,
                        email=normalized_email,
                        name=display_name,
                        token=self._encode_token(user_id),
                        is_admin=self._row_is_admin(existing_by_email),
                    )

                user_id = f"usr_{uuid4().hex[:12]}"
                is_admin = normalized_email in self.admin_emails
                self._execute(
                    conn,
                    """
                    INSERT INTO users (
                        id,
                        name,
                        email,
                        is_admin,
                        password_hash,
                        password_salt,
                        auth_provider,
                        provider_user_id,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        display_name,
                        normalized_email,
                        is_admin,
                        "",
                        "",
                        "google",
                        provider_user_id,
                        now,
                        now,
                    ),
                )
                conn.commit()
                return RegisteredUser(
                    user_id=user_id,
                    email=normalized_email,
                    name=display_name,
                    token=self._encode_token(user_id),
                    is_admin=is_admin,
                )

    def create_google_oauth_state(self, *, next_path: str, ttl_seconds: int = 600) -> tuple[str, str]:
        return self.identity.create_google_oauth_state(next_path=next_path, ttl_seconds=ttl_seconds)

    def consume_google_oauth_state(self, *, state: str) -> dict[str, str] | None:
        return self.identity.consume_google_oauth_state(state=state)

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int = MAX_UPLOAD_SIZE_BYTES) -> None:
        self.quota.assert_upload_size(raw_bytes, max_upload_size_bytes)

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None:
        self.quota.ensure_quota_available(identity, required_units=required_units)

    def consume_quota(self, identity: IdentityContext, *, consumed_units: int = 1) -> int:
        return self.quota.consume_quota(identity, consumed_units=consumed_units)

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
            created_at=created_at,
            expires_at=expires_at,
        )

    def list_user_conversions(self, *, user_id: str, limit: int = 20) -> list[dict[str, str | int]]:
        return self.checkout.list_user_conversions(user_id=user_id, limit=limit)

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
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | bool]], int]:
        return self.admin.list_users_for_admin(
            query=query,
            only_admin=only_admin,
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

    def list_user_role_events_for_admin(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, str | bool | None]]:
        return self.admin.list_user_role_events_for_admin(user_id=user_id, limit=limit)

    def _ensure_anonymous_identity(self, fingerprint: str) -> str:
        now = self.now_provider().isoformat()
        with self._lock:
            with self._connect() as conn:
                existing = self._fetchone(
                    conn,
                    "SELECT id FROM anonymous_identities WHERE fingerprint = ?",
                    (fingerprint,),
                )
                if existing is not None:
                    anon_id = str(existing["id"])
                    self._execute(
                        conn,
                        "UPDATE anonymous_identities SET last_seen_at = ? WHERE id = ?",
                        (now, anon_id),
                    )
                    conn.commit()
                    return anon_id
                anon_id = f"anon_{uuid4().hex[:12]}"
                self._execute(
                    conn,
                    """
                    INSERT INTO anonymous_identities (id, fingerprint, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (anon_id, fingerprint, now, now),
                )
                conn.commit()
                return anon_id

    def _read_active_user_plan(self, *, user_id: str) -> dict[str, str | int] | None:
        if self.active_plan_cache_ttl_seconds > 0:
            cached = self._active_plan_cache.get(user_id)
            now_monotonic = time.monotonic()
            if cached is not None and cached[0] > now_monotonic:
                return cached[1]

        with self._lock:
            with self._connect() as conn:
                plan = read_active_user_plan_query(
                    conn,
                    fetchone=self._fetchone,
                    user_id=user_id,
                )
                if self.active_plan_cache_ttl_seconds > 0:
                    expires_at = time.monotonic() + float(self.active_plan_cache_ttl_seconds)
                    self._active_plan_cache[user_id] = (expires_at, plan)
                return plan

    def _read_usage(self, identity: IdentityContext) -> dict[str, int | datetime]:
        with self._lock:
            with self._connect() as conn:
                snapshot = read_usage_snapshot(
                    conn,
                    identity=identity,
                    now_provider=self.now_provider,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    parse_usage_datetime=self._parse_usage_datetime,
                    is_quota_window_expired=self._is_quota_window_expired,
                )
                conn.commit()
                return {
                    "used_count": int(snapshot.used_count),
                    "window_started_at": snapshot.window_started_at,
                }

    def _invalidate_active_plan_cache(self, user_id: str) -> None:
        self._active_plan_cache.pop(user_id, None)

    def _hash_password(self, password: str, salt: str) -> str:
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PASSWORD_HASH_ITERATIONS,
        )
        return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${base64.b64encode(derived_key).decode('ascii')}"

    def _verify_password(self, password: str, stored_hash: str, stored_salt: str) -> bool:
        if not stored_hash or not stored_salt:
            return False
        expected_hash = self._hash_password(password=password, salt=stored_salt)
        return hmac.compare_digest(expected_hash, stored_hash)

    def _create_user_session_bundle(
        self,
        *,
        user: RegisteredUser,
        ip_address: str | None,
        user_agent: str | None,
    ) -> SessionTokenBundle:
        now = self.now_provider()
        with self._lock:
            with self._connect() as conn:
                bundle = self._create_user_session_bundle_with_conn(
                    conn=conn,
                    user=user,
                    family_id=f"fam_{uuid4().hex}",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    now=now,
                )
                conn.commit()
                return bundle

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
        session_id = f"ses_{uuid4().hex}"
        access_expires_at = now + timedelta(seconds=self.session_access_token_ttl_seconds)
        refresh_expires_at = now + timedelta(seconds=self.session_refresh_token_ttl_seconds)
        access_token = self._encode_session_token(
            user_id=user.user_id,
            session_id=session_id,
            token_type="access",
            expires_at=access_expires_at,
        )
        refresh_token = self._encode_session_token(
            user_id=user.user_id,
            session_id=session_id,
            token_type="refresh",
            expires_at=refresh_expires_at,
        )
        refresh_hash = self._hash_refresh_token(refresh_token)
        self._execute(
            conn,
            """
            INSERT INTO user_sessions (
                id,
                user_id,
                refresh_token_hash,
                refresh_token_family,
                created_at,
                expires_at,
                rotated_at,
                revoked_at,
                replaced_by_session_id,
                revoke_reason,
                last_ip,
                last_user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user.user_id,
                refresh_hash,
                family_id,
                now.isoformat(),
                refresh_expires_at.isoformat(),
                None,
                None,
                None,
                None,
                (ip_address or "").strip() or None,
                (user_agent or "").strip()[:512] or None,
            ),
        )
        return SessionTokenBundle(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires_at.isoformat(),
            refresh_expires_at=refresh_expires_at.isoformat(),
        )

    def _revoke_session_family_with_conn(self, conn, *, family_id: str, reason: str, now_iso: str) -> None:
        normalized_family = str(family_id or "").strip()
        if not normalized_family:
            return
        self._execute(
            conn,
            """
            UPDATE user_sessions
            SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = COALESCE(revoke_reason, ?)
            WHERE refresh_token_family = ?
            """,
            (now_iso, reason, normalized_family),
        )

    def _get_registered_user_by_id(self, *, user_id: str) -> RegisteredUser:
        with self._lock:
            with self._connect() as conn:
                return self._get_registered_user_by_id_with_conn(conn=conn, user_id=user_id)

    def _get_registered_user_by_id_with_conn(self, *, conn, user_id: str) -> RegisteredUser:
        row = self._fetchone(
            conn,
            "SELECT id, name, email, is_admin FROM users WHERE id = ?",
            (user_id,),
        )
        if row is None:
            raise InvalidSessionTokenError
        return RegisteredUser(
            user_id=str(row["id"]),
            email=str(row["email"]),
            name=str(row["name"] or ""),
            token=self._encode_token(str(row["id"])),
            is_admin=self._row_is_admin(row),
        )

    def _hash_refresh_token(self, refresh_token: str) -> str:
        normalized = str(refresh_token or "").strip()
        if not normalized:
            return ""
        return hmac.new(self.token_secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    def _encode_session_token(self, *, user_id: str, session_id: str, token_type: str, expires_at: datetime) -> str:
        payload_obj = {
            "uid": user_id,
            "sid": session_id,
            "typ": token_type,
            "exp": int(expires_at.timestamp()),
            "iat": int(self.now_provider().timestamp()),
            "jti": uuid4().hex,
        }
        payload_json = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)
        payload = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")
        signature = hmac.new(self.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
        return f"{payload}.{signature}"

    def _decode_session_token(self, token: str, *, expected_type: str) -> dict[str, str | int]:
        raw = str(token or "").strip()
        try:
            payload, signature = raw.split(".", 1)
        except ValueError:
            raise InvalidSessionTokenError from None
        expected_signature = hmac.new(self.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(expected_signature, signature):
            raise InvalidSessionTokenError
        try:
            padded_payload = payload + "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(padded_payload.encode("utf-8")).decode("utf-8")
            payload_obj = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise InvalidSessionTokenError from None
        token_type = str(payload_obj.get("typ") or "").strip()
        user_id = str(payload_obj.get("uid") or "").strip()
        session_id = str(payload_obj.get("sid") or "").strip()
        exp_raw = payload_obj.get("exp")
        if token_type != expected_type or not user_id.startswith("usr_") or not session_id.startswith("ses_"):
            raise InvalidSessionTokenError
        try:
            exp_unix = int(exp_raw)
        except (TypeError, ValueError):
            raise InvalidSessionTokenError from None
        now_unix = int(self.now_provider().timestamp())
        if exp_unix <= now_unix:
            raise InvalidSessionTokenError
        return payload_obj

    def _extract_session_id_from_token(self, refresh_token: str) -> str:
        payload = self._decode_session_token(refresh_token, expected_type="refresh")
        session_id = str(payload.get("sid") or "").strip()
        if not session_id:
            raise InvalidSessionTokenError
        return session_id

    def _encode_token(self, user_id: str) -> str:
        payload = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("utf-8").rstrip("=")
        signature = hmac.new(self.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return f"{payload}.{signature}"

    def _decode_token(self, token: str) -> str:
        try:
            payload, signature = token.split(".", 1)
            expected = hmac.new(self.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
            if not hmac.compare_digest(expected, signature):
                raise InvalidUserTokenError
            padded_payload = payload + "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(padded_payload.encode("utf-8")).decode("utf-8")
            if not decoded.startswith("usr_"):
                raise InvalidUserTokenError
            return decoded
        except (ValueError, UnicodeDecodeError):
            raise InvalidUserTokenError from None

    def _user_exists(self, user_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(conn, "SELECT id FROM users WHERE id = ?", (user_id,))
                return row is not None

    @contextmanager
    def _connect(self) -> Iterator:
        if self._use_postgres:
            assert psycopg is not None and dict_row is not None
            last_exc: Exception | None = None
            for attempt in range(1, self.db_connect_retry_attempts + 1):
                try:
                    if self._postgres_pool is not None:
                        with self._postgres_pool.connection(timeout=self.db_pool_timeout_seconds) as conn:
                            with conn.cursor() as cur:
                                cur.execute(f'SET search_path TO "{self.database_schema}", public')
                            yield conn
                            return

                    with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
                        with conn.cursor() as cur:
                            cur.execute(f'SET search_path TO "{self.database_schema}", public')
                        yield conn
                        return
                except Exception as exc:  # pragma: no cover - exercised in postgres environments
                    last_exc = exc
                    if attempt >= self.db_connect_retry_attempts or not self._is_retryable_db_exception(exc):
                        raise
                    sleep_seconds = (self.db_connect_retry_base_ms / 1000.0) * (2 ** (attempt - 1))
                    time.sleep(min(sleep_seconds, 2.0))

            if last_exc is not None:  # pragma: no cover - safety fallback
                raise last_exc
            raise RuntimeError("Failed to establish database connection.")

        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _is_retryable_db_exception(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retryable_hints = (
            "failed to acquire permit to connect",
            "too many database connection attempts",
            "connection timeout",
            "network is unreachable",
            "control plane request failed",
            "timeout expired",
            "could not connect",
            "connection refused",
        )
        return any(token in message for token in retryable_hints)

    def _init_db(self) -> None:
        if self._use_postgres:
            self._init_postgres_db()
            return

        with self._lock:
            with self._connect() as conn:
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
                self._sync_admin_emails(conn)
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
                    fetchone=self._fetchone,
                    execute=self._execute,
                    now_iso=self.now_provider().isoformat(),
                    true_value=self._true_value(),
                )
                conn.commit()

    def _init_postgres_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.database_schema}"')
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            email TEXT NOT NULL UNIQUE,
                            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                            password_hash TEXT NOT NULL,
                            password_salt TEXT NOT NULL,
                            auth_provider TEXT NOT NULL DEFAULT 'local',
                            provider_user_id TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS anonymous_identities (
                            id TEXT PRIMARY KEY,
                            fingerprint TEXT NOT NULL UNIQUE,
                            created_at TEXT NOT NULL,
                            last_seen_at TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS usage (
                            identity_type TEXT NOT NULL,
                            identity_id TEXT NOT NULL,
                            used_count INTEGER NOT NULL,
                            quota_limit INTEGER NOT NULL,
                            updated_at TEXT NOT NULL,
                            window_started_at TEXT,
                            PRIMARY KEY (identity_type, identity_id)
                        )
                        """
                    )
                    cur.execute(
                        """
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
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS google_oauth_states (
                            state TEXT PRIMARY KEY,
                            code_verifier TEXT NOT NULL,
                            next_path TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
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
                        )
                        """
                    )
                    cur.execute(
                        """
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
                            is_public BOOLEAN NOT NULL DEFAULT TRUE,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_plan_subscriptions (
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            plan_version_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            started_at TEXT NOT NULL,
                            ended_at TEXT,
                            FOREIGN KEY(user_id) REFERENCES users(id),
                            FOREIGN KEY(plan_version_id) REFERENCES plan_versions(id)
                        )
                        """
                    )
                    cur.execute(
                        """
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
                        )
                        """
                    )
                    cur.execute(
                        """
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
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS admin_user_role_events (
                            id TEXT PRIMARY KEY,
                            target_user_id TEXT NOT NULL,
                            target_email TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            actor_user_id TEXT,
                            actor_email TEXT,
                            previous_is_admin BOOLEAN NOT NULL,
                            new_is_admin BOOLEAN NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(target_user_id) REFERENCES users(id),
                            FOREIGN KEY(actor_user_id) REFERENCES users(id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_provider_user_id
                        ON users(provider_user_id)
                        WHERE auth_provider = 'google' AND provider_user_id IS NOT NULL
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_conversions_user_created_at
                        ON user_conversions(user_id, created_at DESC)
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_sessions_user_created_at
                        ON user_sessions(user_id, created_at DESC)
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_sessions_family
                        ON user_sessions(refresh_token_family)
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
                        ON user_sessions(expires_at)
                        """
                    )
                    cur.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_versions_code_version
                        ON plan_versions(code, version)
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_plan_subscriptions_user_active
                        ON user_plan_subscriptions(user_id, status)
                        """
                    )
                    # Ensure a single active subscription per user before enforcing uniqueness.
                    cur.execute(
                        """
                        WITH ranked AS (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY user_id
                                    ORDER BY started_at DESC, id DESC
                                ) AS rn
                            FROM user_plan_subscriptions
                            WHERE status = 'active'
                        )
                        UPDATE user_plan_subscriptions ups
                        SET
                            status = 'ended',
                            ended_at = COALESCE(ups.ended_at, NOW()::text)
                        FROM ranked
                        WHERE ups.id = ranked.id AND ranked.rn > 1
                        """
                    )
                    cur.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_plan_subscriptions_one_active_per_user
                        ON user_plan_subscriptions(user_id)
                        WHERE status = 'active'
                        """
                    )
                    cur.execute(
                        """
                        UPDATE users
                        SET auth_provider = 'local'
                        WHERE auth_provider IS NULL OR auth_provider = ''
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
                        """
                    )
                    self._sync_admin_emails(conn)
                    cur.execute(
                        """
                        UPDATE usage
                        SET window_started_at = updated_at
                        WHERE window_started_at IS NULL OR window_started_at = ''
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE user_conversions
                        ADD COLUMN IF NOT EXISTS pages_count INTEGER
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE checkout_intents
                        ADD COLUMN IF NOT EXISTS user_id TEXT
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE checkout_intents
                        ADD COLUMN IF NOT EXISTS payment_link TEXT
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE checkout_intents
                        ADD COLUMN IF NOT EXISTS payment_link_sent_at TEXT
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE checkout_intents
                        ADD COLUMN IF NOT EXISTS released_at TEXT
                        """
                    )
                    cur.execute(
                        """
                        UPDATE checkout_intents
                        SET status = %s
                        WHERE status = %s
                        """,
                        (CHECKOUT_STATUS_REQUESTED, CHECKOUT_STATUS_PENDING_LEGACY),
                    )
                    seed_default_public_plans(
                        conn,
                        fetchone=self._fetchone,
                        execute=self._execute,
                        now_iso=self.now_provider().isoformat(),
                        true_value=self._true_value(),
                    )
                conn.commit()

    def _is_quota_window_expired(self, window_started_at: datetime, now: datetime, *, quota_window_days: int) -> bool:
        return now >= (window_started_at + timedelta(days=max(1, int(quota_window_days))))

    def _parse_usage_datetime(self, raw_value: str, fallback: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _normalize_next_path(self, next_path: str | None) -> str:
        raw = str(next_path or "").strip()
        if not raw.startswith("/"):
            return "/client-area.html"
        return raw

    def _adapt_query(self, query: str) -> str:
        if self._use_postgres:
            return query.replace("?", "%s")
        return query

    def _true_value(self):
        if self._use_postgres:
            return True
        return 1

    def _false_value(self):
        if self._use_postgres:
            return False
        return 0

    def _execute(self, conn, query: str, params: tuple = ()):
        adapted = self._adapt_query(query)
        if self._use_postgres:
            cur = conn.cursor()
            cur.execute(adapted, params)
            return cur
        return conn.execute(adapted, params)

    def _fetchone(self, conn, query: str, params: tuple = ()):
        cur = self._execute(conn, query, params)
        if self._use_postgres:
            try:
                return cur.fetchone()
            finally:
                cur.close()
        return cur.fetchone()

    def _fetchall(self, conn, query: str, params: tuple = ()):
        cur = self._execute(conn, query, params)
        if self._use_postgres:
            try:
                return cur.fetchall()
            finally:
                cur.close()
        return cur.fetchall()

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
        normalized_intent_id = str(intent_id or "").strip()
        normalized_event_type = str(event_type or "").strip().upper()
        normalized_actor_kind = str(actor_kind or "system").strip().lower() or "system"
        if not normalized_intent_id or not normalized_event_type:
            return
        payload_json: str | None = None
        if payload:
            payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        insert_checkout_intent_event_query(
            conn,
            execute=self._execute,
            event_id=f"evt_{uuid4().hex[:16]}",
            intent_id=normalized_intent_id,
            event_type=normalized_event_type,
            event_message=str(event_message or "").strip(),
            actor_kind=normalized_actor_kind,
            actor_user_id=(str(actor_user_id).strip() if actor_user_id else None),
            payload_json=payload_json,
            created_at=created_at,
        )

    def _normalize_admin_emails(self, emails: set[str] | None) -> set[str]:
        if not emails:
            return set()
        normalized: set[str] = set()
        for email in emails:
            value = str(email or "").strip().lower()
            if value:
                normalized.add(value)
        return normalized

    def _sync_admin_emails(self, conn) -> None:
        if not self.admin_emails:
            return
        for email in self.admin_emails:
            self._execute(
                conn,
                "UPDATE users SET is_admin = ? WHERE lower(email) = ?",
                (self._true_value(), email),
            )

    def _row_is_admin(self, row) -> bool:
        if row is None:
            return False
        keys = row.keys() if hasattr(row, "keys") else ()
        if "is_admin" not in keys:
            return False
        return self._row_bool_from_value(row["is_admin"])

    def _row_bool_from_value(self, raw) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        return str(raw or "").strip().lower() in {"1", "true", "t", "yes"}

    def _normalize_database_schema(self, schema: str | None) -> str:
        raw = (schema or "public").strip()
        if not raw:
            return "public"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
            raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
        return raw
