from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidUserTokenError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, IdentityContext


class AccessControlIdentityComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def resolve_identity(
        self,
        anonymous_fingerprint: str | None,
        user_token: str | None,
    ) -> IdentityContext:
        service = self._service
        if user_token:
            user_id = service._decode_token(user_token)
            if not service._user_exists(user_id):
                raise InvalidUserTokenError
            active_plan = service._read_active_user_plan(user_id=user_id)
            if active_plan is not None:
                return service._identity_context_factory(
                    identity_type="user",
                    identity_id=user_id,
                    quota_limit=int(active_plan["quota_limit"]),
                    quota_mode=str(active_plan["quota_mode"]),
                    quota_window_days=int(active_plan["quota_window_days"]),
                    max_upload_size_bytes=int(active_plan["max_upload_size_bytes"]),
                    max_pages_per_file=int(active_plan["max_pages_per_file"]),
                    max_pages_per_file_ocr=int(active_plan["max_pages_per_file_ocr"]),
                    plan_code=str(active_plan["code"]),
                    plan_name=str(active_plan["name"]),
                )
            return service._identity_context_factory(
                identity_type="user",
                identity_id=user_id,
                quota_limit=service.registered_quota_limit,
                quota_mode="conversion",
                quota_window_days=service.quota_window_days,
            )

        fingerprint = (anonymous_fingerprint or "").strip()
        if not fingerprint:
            raise InvalidUserTokenError
        anon_id = service._ensure_anonymous_identity(fingerprint)
        return service._identity_context_factory(
            identity_type="anonymous",
            identity_id=anon_id,
            quota_limit=service.anonymous_quota_limit,
            quota_mode="conversion",
            quota_window_days=service.quota_window_days,
        )

    def create_google_oauth_state(self, *, next_path: str, ttl_seconds: int = 600) -> tuple[str, str]:
        state = f"gst_{secrets.token_urlsafe(24)}"
        code_verifier = secrets.token_urlsafe(64)
        now = self._service.now_provider()
        expires_at = (now + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat()
        with self._service._lock:
            with self._service._connect() as conn:
                self._service._execute(
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
                        self._service._normalize_next_path(next_path),
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
        now = self._service.now_provider()
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    """
                    SELECT state, code_verifier, next_path, expires_at
                    FROM google_oauth_states
                    WHERE state = ?
                    """,
                    (normalized_state,),
                )
                self._service._execute(conn, "DELETE FROM google_oauth_states WHERE state = ?", (normalized_state,))
                self._service._execute(conn, "DELETE FROM google_oauth_states WHERE expires_at < ?", (now.isoformat(),))
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
            "next_path": self._service._normalize_next_path(str(row["next_path"])),
        }

    def ensure_anonymous_identity(self, fingerprint: str) -> str:
        service = self._service
        now = service.now_provider().isoformat()
        with service._lock:
            with service._connect() as conn:
                existing = service._fetchone(
                    conn,
                    "SELECT id FROM anonymous_identities WHERE fingerprint = ?",
                    (fingerprint,),
                )
                if existing is not None:
                    anon_id = str(existing["id"])
                    service._execute(
                        conn,
                        "UPDATE anonymous_identities SET last_seen_at = ? WHERE id = ?",
                        (now, anon_id),
                    )
                    conn.commit()
                    return anon_id
                anon_id = f"anon_{uuid4().hex[:12]}"
                service._execute(
                    conn,
                    """
                    INSERT INTO anonymous_identities (id, fingerprint, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (anon_id, fingerprint, now, now),
                )
                conn.commit()
                return anon_id

    def normalize_next_path(self, next_path: str | None) -> str:
        raw = str(next_path or "").strip()
        if not raw.startswith("/"):
            return "/client-area.html"
        return raw
