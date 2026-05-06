import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency for postgres deployments
    psycopg = None
    dict_row = None

from app.application.checkout_management import (
    CHECKOUT_STATUS_PENDING_LEGACY,
    CHECKOUT_STATUS_REQUESTED,
)
from app.application.checkout_management import (
    create_checkout_intent as create_checkout_intent_query,
)
from app.application.checkout_management import (
    insert_checkout_intent_event as insert_checkout_intent_event_query,
)
from app.application.checkout_management import (
    list_checkout_intent_events_for_admin as list_checkout_intent_events_for_admin_query,
)
from app.application.checkout_management import (
    list_checkout_intents_for_admin as list_checkout_intents_for_admin_query,
)
from app.application.checkout_management import (
    mark_checkout_intent_awaiting_payment as mark_checkout_intent_awaiting_payment_query,
)
from app.application.checkout_management import (
    mark_checkout_intent_released_by_id as mark_checkout_intent_released_by_id_query,
)
from app.application.checkout_management import (
    mark_latest_checkout_intent_released_for_user_plan as mark_latest_checkout_intent_released_for_user_plan_query,
)
from app.application.checkout_management import (
    read_checkout_intent_by_id as read_checkout_intent_by_id_query,
)
from app.application.checkout_management import (
    read_checkout_intent_for_user as read_checkout_intent_for_user_query,
)
from app.application.checkout_management import (
    read_latest_checkout_intent_for_user as read_latest_checkout_intent_for_user_query,
)
from app.application.conversion_history import (
    list_user_conversions as list_user_conversions_query,
)
from app.application.conversion_history import (
    record_user_conversion as record_user_conversion_query,
)
from app.application.errors import (
    FileTooLargeError,
    InvalidCredentialsError,
    InvalidUserTokenError,
    UserAlreadyExistsError,
)
from app.application.plan_management import (
    activate_user_plan as activate_user_plan_query,
)
from app.application.plan_management import (
    list_public_plans as list_public_plans_query,
)
from app.application.plan_management import (
    read_active_user_plan as read_active_user_plan_query,
)
from app.application.plan_management import (
    seed_default_public_plans,
)
from app.application.quota_management import (
    compute_quota_reset_at,
    compute_remaining_quota,
    persist_consumed_usage,
    read_usage_snapshot,
    require_quota_available,
)

ANONYMOUS_QUOTA_LIMIT = 3
REGISTERED_QUOTA_LIMIT = 10
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024
PASSWORD_HASH_ITERATIONS = 390_000
QUOTA_WINDOW_DAYS = 7


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
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        if not self._use_postgres:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
        elif psycopg is None:
            raise RuntimeError("PostgreSQL support requires psycopg. Install backend requirements.")
        self._init_db()

    def resolve_identity(
        self,
        anonymous_fingerprint: str | None,
        user_token: str | None,
    ) -> IdentityContext:
        if user_token:
            user_id = self._decode_token(user_token)
            if not self._user_exists(user_id):
                raise InvalidUserTokenError
            active_plan = self._read_active_user_plan(user_id=user_id)
            if active_plan is not None:
                return IdentityContext(
                    identity_type="user",
                    identity_id=user_id,
                    quota_limit=int(active_plan["quota_limit"]),
                    quota_mode=str(active_plan["quota_mode"]),
                    quota_window_days=int(active_plan["quota_window_days"]),
                    max_upload_size_bytes=int(active_plan["max_upload_size_bytes"]),
                    max_pages_per_file=int(active_plan["max_pages_per_file"]),
                    plan_code=str(active_plan["code"]),
                    plan_name=str(active_plan["name"]),
                )
            return IdentityContext(
                identity_type="user",
                identity_id=user_id,
                quota_limit=self.registered_quota_limit,
                quota_mode="conversion",
                quota_window_days=self.quota_window_days,
            )

        fingerprint = (anonymous_fingerprint or "").strip()
        if not fingerprint:
            raise InvalidUserTokenError
        anon_id = self._ensure_anonymous_identity(fingerprint)
        return IdentityContext(
            identity_type="anonymous",
            identity_id=anon_id,
            quota_limit=self.anonymous_quota_limit,
            quota_mode="conversion",
            quota_window_days=self.quota_window_days,
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
        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(
                    conn,
                    "SELECT is_admin FROM users WHERE id = ?",
                    (user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                return self._row_is_admin(row)

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
        state = f"gst_{secrets.token_urlsafe(24)}"
        code_verifier = secrets.token_urlsafe(64)
        now = self.now_provider()
        expires_at = (now + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat()
        with self._lock:
            with self._connect() as conn:
                self._execute(
                    conn,
                    """
                    INSERT INTO google_oauth_states (
                        state,
                        code_verifier,
                        next_path,
                        created_at,
                        expires_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state,
                        code_verifier,
                        self._normalize_next_path(next_path),
                        now.isoformat(),
                        expires_at,
                    ),
                )
                conn.commit()
        return state, code_verifier

    def consume_google_oauth_state(self, *, state: str) -> dict[str, str] | None:
        normalized_state = state.strip()
        if not normalized_state:
            return None
        now = self.now_provider()
        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(
                    conn,
                    """
                    SELECT state, code_verifier, next_path, expires_at
                    FROM google_oauth_states
                    WHERE state = ?
                    """,
                    (normalized_state,),
                )
                self._execute(conn, "DELETE FROM google_oauth_states WHERE state = ?", (normalized_state,))
                self._execute(conn, "DELETE FROM google_oauth_states WHERE expires_at < ?", (now.isoformat(),))
                conn.commit()

        if row is None:
            return None

        expires_at_raw = str(row["expires_at"] or "").strip()
        if not expires_at_raw:
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return None

        return {
            "state": str(row["state"]),
            "code_verifier": str(row["code_verifier"]),
            "next_path": self._normalize_next_path(str(row["next_path"])),
        }

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int = MAX_UPLOAD_SIZE_BYTES) -> None:
        if len(raw_bytes) > max(1, int(max_upload_size_bytes)):
            raise FileTooLargeError

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None:
        usage = self._read_usage(identity)
        require_quota_available(
            used_count=int(usage["used_count"]),
            quota_limit=identity.quota_limit,
            required_units=required_units,
        )

    def consume_quota(self, identity: IdentityContext, *, consumed_units: int = 1) -> int:
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
                next_snapshot = persist_consumed_usage(
                    conn,
                    identity=identity,
                    snapshot=snapshot,
                    consumed_units=consumed_units,
                    now_provider=self.now_provider,
                    execute=self._execute,
                )
                conn.commit()
            return compute_remaining_quota(
                used_count=next_snapshot.used_count,
                quota_limit=identity.quota_limit,
            )

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        usage = self._read_usage(identity)
        return compute_remaining_quota(
            used_count=int(usage["used_count"]),
            quota_limit=identity.quota_limit,
        )

    def get_quota_reset_at(self, identity: IdentityContext) -> str:
        usage = self._read_usage(identity)
        return compute_quota_reset_at(
            window_started_at=usage["window_started_at"],
            quota_window_days=identity.quota_window_days,
        )

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
        with self._lock:
            with self._connect() as conn:
                record_user_conversion_query(
                    conn,
                    execute=self._execute,
                    now_iso=self.now_provider().isoformat(),
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
                conn.commit()

    def list_user_conversions(self, *, user_id: str, limit: int = 20) -> list[dict[str, str | int]]:
        with self._lock:
            with self._connect() as conn:
                return list_user_conversions_query(
                    conn,
                    fetchall=self._fetchall,
                    now_provider=self.now_provider,
                    user_id=user_id,
                    limit=limit,
                )

    def list_public_plans(self) -> list[dict[str, str | int]]:
        with self._lock:
            with self._connect() as conn:
                return list_public_plans_query(
                    conn,
                    fetchall=self._fetchall,
                    true_value=self._true_value(),
                )

    def activate_user_plan(
        self,
        *,
        user_id: str,
        plan_code: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int]:
        now_iso = self.now_provider().isoformat()

        with self._lock:
            with self._connect() as conn:
                activated = activate_user_plan_query(
                    conn,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    true_value=self._true_value(),
                    user_id=user_id,
                    plan_code=plan_code,
                    now_iso=now_iso,
                    subscription_id=f"sub_{uuid4().hex[:16]}",
                )
                released_intent_id = mark_latest_checkout_intent_released_for_user_plan_query(
                    conn,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    now_iso=now_iso,
                    user_id=user_id,
                    plan_code=plan_code,
                )
                if released_intent_id:
                    self._append_checkout_intent_event_with_conn(
                        conn,
                        intent_id=released_intent_id,
                        event_type="PLAN_RELEASED",
                        event_message="Plan released for user.",
                        actor_kind=actor_kind,
                        actor_user_id=actor_user_id,
                        payload={
                            "user_id": user_id,
                            "plan_code": plan_code,
                        },
                        created_at=now_iso,
                    )
                conn.commit()
        return activated

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
        now_iso = self.now_provider().isoformat()
        intent_id = f"chk_{uuid4().hex[:16]}"
        with self._lock:
            with self._connect() as conn:
                intent = create_checkout_intent_query(
                    conn,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    true_value=self._true_value(),
                    now_iso=now_iso,
                    intent_id=intent_id,
                    user_id=user_id,
                    plan_code=plan_code,
                    customer_name=customer_name,
                    customer_email=customer_email,
                    customer_whatsapp=customer_whatsapp,
                    customer_document=customer_document,
                    customer_notes=customer_notes,
                )
                self._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="ORDER_REQUESTED",
                    event_message="Checkout order requested.",
                    actor_kind="customer",
                    actor_user_id=user_id,
                    payload={
                        "plan_code": str(intent["plan_code"]),
                        "customer_email": customer_email,
                    },
                    created_at=now_iso,
                )
                conn.commit()
        return intent

    def read_checkout_intent_for_user(
        self,
        *,
        intent_id: str,
        user_id: str,
        customer_email: str | None = None,
    ) -> dict[str, str | int | None] | None:
        with self._lock:
            with self._connect() as conn:
                return read_checkout_intent_for_user_query(
                    conn,
                    fetchone=self._fetchone,
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
        with self._lock:
            with self._connect() as conn:
                return read_latest_checkout_intent_for_user_query(
                    conn,
                    fetchone=self._fetchone,
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
        now_iso = self.now_provider().isoformat()
        with self._lock:
            with self._connect() as conn:
                intent = mark_checkout_intent_awaiting_payment_query(
                    conn,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    now_iso=now_iso,
                    intent_id=intent_id,
                    payment_link=payment_link,
                )
                self._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="PAYMENT_LINK_SENT",
                    event_message="Payment link sent to customer.",
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    payload={
                        "payment_link": payment_link,
                    },
                    created_at=now_iso,
                )
                conn.commit()
                return intent

    def read_checkout_intent_by_id(self, *, intent_id: str) -> dict[str, str | int | None] | None:
        with self._lock:
            with self._connect() as conn:
                return read_checkout_intent_by_id_query(
                    conn,
                    fetchone=self._fetchone,
                    intent_id=intent_id,
                )

    def mark_checkout_intent_released_by_id(
        self,
        *,
        intent_id: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int | None]:
        now_iso = self.now_provider().isoformat()
        with self._lock:
            with self._connect() as conn:
                intent = mark_checkout_intent_released_by_id_query(
                    conn,
                    fetchone=self._fetchone,
                    execute=self._execute,
                    now_iso=now_iso,
                    intent_id=intent_id,
                )
                self._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="PLAN_RELEASED",
                    event_message="Plan released for user.",
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    payload={
                        "plan_code": str(intent["plan_code"]),
                    },
                    created_at=now_iso,
                )
                conn.commit()
                return intent

    def list_checkout_intents_for_admin(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | int | None]], int]:
        with self._lock:
            with self._connect() as conn:
                return list_checkout_intents_for_admin_query(
                    conn,
                    fetchall=self._fetchall,
                    fetchone=self._fetchone,
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
        with self._lock:
            with self._connect() as conn:
                return list_checkout_intent_events_for_admin_query(
                    conn,
                    fetchall=self._fetchall,
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
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip().lower()

        with self._lock:
            with self._connect() as conn:
                where: list[str] = []
                params: list[str | int] = []
                if only_admin is True:
                    where.append("is_admin = ?")
                    params.append(self._true_value())
                elif only_admin is False:
                    where.append("is_admin = ?")
                    params.append(self._false_value())
                if normalized_query:
                    where.append("(lower(name) LIKE ? OR lower(email) LIKE ? OR lower(id) LIKE ?)")
                    like = f"%{normalized_query}%"
                    params.extend([like, like, like])

                base = "FROM users"
                if where:
                    base += " WHERE " + " AND ".join(where)

                total_row = self._fetchone(conn, f"SELECT COUNT(1) AS total {base}", tuple(params))
                total = int(total_row["total"]) if total_row is not None else 0
                rows = self._fetchall(
                    conn,
                    f"SELECT id, name, email, is_admin, created_at, updated_at {base} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    tuple(params + [normalized_limit, normalized_offset]),
                )
                items: list[dict[str, str | bool]] = []
                for row in rows:
                    items.append(
                        {
                            "user_id": str(row["id"]),
                            "name": str(row["name"] or ""),
                            "email": str(row["email"] or ""),
                            "is_admin": self._row_is_admin(row),
                            "created_at": str(row["created_at"] or ""),
                            "updated_at": str(row["updated_at"] or ""),
                        }
                    )
                return items, total

    def set_user_admin_role(self, *, user_id: str, is_admin: bool) -> dict[str, str | bool]:
        return self.set_user_admin_role_with_actor(
            user_id=user_id,
            is_admin=is_admin,
            actor_user_id=None,
        )

    def set_user_admin_role_with_actor(
        self,
        *,
        user_id: str,
        is_admin: bool,
        actor_user_id: str | None,
    ) -> dict[str, str | bool]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise InvalidUserTokenError
        now_iso = self.now_provider().isoformat()

        with self._lock:
            with self._connect() as conn:
                row = self._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin, created_at, updated_at FROM users WHERE id = ?",
                    (normalized_user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                previous_is_admin = self._row_is_admin(row)
                self._execute(
                    conn,
                    "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
                    (self._true_value() if is_admin else self._false_value(), now_iso, normalized_user_id),
                )
                actor_email: str | None = None
                if actor_user_id:
                    actor_row = self._fetchone(
                        conn,
                        "SELECT email FROM users WHERE id = ?",
                        (str(actor_user_id).strip(),),
                    )
                    if actor_row is not None:
                        actor_email = str(actor_row["email"] or "") or None

                event_type = "ADMIN_ROLE_GRANTED" if is_admin else "ADMIN_ROLE_REVOKED"
                self._execute(
                    conn,
                    """
                    INSERT INTO admin_user_role_events (
                      id,
                      target_user_id,
                      target_email,
                      event_type,
                      actor_user_id,
                      actor_email,
                      previous_is_admin,
                      new_is_admin,
                      created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"aur_{uuid4().hex[:16]}",
                        normalized_user_id,
                        str(row["email"] or ""),
                        event_type,
                        (str(actor_user_id).strip() if actor_user_id else None),
                        actor_email,
                        self._true_value() if previous_is_admin else self._false_value(),
                        self._true_value() if is_admin else self._false_value(),
                        now_iso,
                    ),
                )
                conn.commit()
                return {
                    "user_id": str(row["id"]),
                    "name": str(row["name"] or ""),
                    "email": str(row["email"] or ""),
                    "is_admin": bool(is_admin),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": now_iso,
                }

    def list_user_role_events_for_admin(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, str | bool | None]]:
        normalized_user_id = str(user_id or "").strip()
        normalized_limit = max(1, min(int(limit), 500))
        if not normalized_user_id:
            return []
        with self._lock:
            with self._connect() as conn:
                rows = self._fetchall(
                    conn,
                    """
                    SELECT
                      id,
                      target_user_id,
                      target_email,
                      event_type,
                      actor_user_id,
                      actor_email,
                      previous_is_admin,
                      new_is_admin,
                      created_at
                    FROM admin_user_role_events
                    WHERE target_user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (normalized_user_id, normalized_limit),
                )
                items: list[dict[str, str | bool | None]] = []
                for row in rows:
                    items.append(
                        {
                            "event_id": str(row["id"]),
                            "target_user_id": str(row["target_user_id"]),
                            "target_email": str(row["target_email"] or ""),
                            "event_type": str(row["event_type"]),
                            "actor_user_id": str(row["actor_user_id"] or "") or None,
                            "actor_email": str(row["actor_email"] or "") or None,
                            "previous_is_admin": self._row_bool_from_value(row["previous_is_admin"]),
                            "new_is_admin": self._row_bool_from_value(row["new_is_admin"]),
                            "created_at": str(row["created_at"]),
                        }
                    )
                return items

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
        with self._lock:
            with self._connect() as conn:
                return read_active_user_plan_query(
                    conn,
                    fetchone=self._fetchone,
                    user_id=user_id,
                )

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

    def _connect(self) -> sqlite3.Connection:
        if self._use_postgres:
            assert psycopg is not None and dict_row is not None
            conn = psycopg.connect(self.database_url, row_factory=dict_row)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{self.database_schema}", public')
            return conn
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

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
