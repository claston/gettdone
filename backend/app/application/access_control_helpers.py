from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.checkout_management import (
    insert_checkout_intent_event as insert_checkout_intent_event_query,
)
from app.application.errors import InvalidUserTokenError
from app.application.plan_management import (
    read_active_user_plan as read_active_user_plan_query,
)
from app.application.quota_management import (
    read_usage_snapshot,
)

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, IdentityContext

PASSWORD_HASH_ITERATIONS = 390_000


class AccessControlHelpersComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    @staticmethod
    def normalize_database_schema(schema: str | None) -> str:
        raw = (schema or "public").strip()
        if not raw:
            return "public"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
            raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
        return raw

    def read_active_user_plan(self, *, user_id: str) -> dict[str, str | int] | None:
        service = self._service
        if service.active_plan_cache_ttl_seconds > 0:
            cached = service._active_plan_cache.get(user_id)
            now_monotonic = time.monotonic()
            if cached is not None and cached[0] > now_monotonic:
                return cached[1]

        with service._lock:
            with service._connect() as conn:
                plan = read_active_user_plan_query(
                    conn,
                    fetchone=service._fetchone,
                    user_id=user_id,
                )
                if service.active_plan_cache_ttl_seconds > 0:
                    expires_at = time.monotonic() + float(service.active_plan_cache_ttl_seconds)
                    service._active_plan_cache[user_id] = (expires_at, plan)
                return plan

    def read_usage(self, identity: IdentityContext) -> dict[str, int | datetime]:
        service = self._service
        with service._lock:
            with service._connect() as conn:
                snapshot = read_usage_snapshot(
                    conn,
                    identity=identity,
                    now_provider=service.now_provider,
                    fetchone=service._fetchone,
                    execute=service._execute,
                    parse_usage_datetime=self.parse_usage_datetime,
                    is_quota_window_expired=self.is_quota_window_expired,
                )
                conn.commit()
                return {
                    "used_count": int(snapshot.used_count),
                    "window_started_at": snapshot.window_started_at,
                }

    def invalidate_active_plan_cache(self, user_id: str) -> None:
        self._service._active_plan_cache.pop(user_id, None)

    @staticmethod
    def hash_password(password: str, salt: str) -> str:
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PASSWORD_HASH_ITERATIONS,
        )
        return (
            f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}"
            f"${base64.b64encode(derived_key).decode('ascii')}"
        )

    def verify_password(self, password: str, stored_hash: str, stored_salt: str) -> bool:
        if not stored_hash or not stored_salt:
            return False
        expected_hash = self.hash_password(password=password, salt=stored_salt)
        return hmac.compare_digest(expected_hash, stored_hash)

    def encode_token(self, user_id: str) -> str:
        payload = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("utf-8").rstrip("=")
        signature = hmac.new(self._service.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return f"{payload}.{signature}"

    def decode_token(self, token: str) -> str:
        try:
            payload, signature = token.split(".", 1)
            expected = hmac.new(self._service.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
            if not hmac.compare_digest(expected, signature):
                raise InvalidUserTokenError
            padded_payload = payload + "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(padded_payload.encode("utf-8")).decode("utf-8")
            if not decoded.startswith("usr_"):
                raise InvalidUserTokenError
            return decoded
        except (ValueError, UnicodeDecodeError):
            raise InvalidUserTokenError from None

    def append_checkout_intent_event_with_conn(
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
        service = self._service
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
            execute=service._execute,
            event_id=f"evt_{uuid4().hex[:16]}",
            intent_id=normalized_intent_id,
            event_type=normalized_event_type,
            event_message=str(event_message or "").strip(),
            actor_kind=normalized_actor_kind,
            actor_user_id=(str(actor_user_id).strip() if actor_user_id else None),
            payload_json=payload_json,
            created_at=created_at,
        )

    @staticmethod
    def is_quota_window_expired(window_started_at: datetime, now: datetime, *, quota_window_days: int) -> bool:
        return now >= (window_started_at + timedelta(days=max(1, int(quota_window_days))))

    @staticmethod
    def parse_usage_datetime(raw_value: str, fallback: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
