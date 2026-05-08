from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidSessionTokenError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, RegisteredUser, SessionTokenBundle


class AccessControlSessionCoreComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def create_user_session_bundle(
        self,
        *,
        user: RegisteredUser,
        ip_address: str | None,
        user_agent: str | None,
    ) -> SessionTokenBundle:
        now = self._service.now_provider()
        with self._service._lock:
            with self._service._connect() as conn:
                bundle = self.create_user_session_bundle_with_conn(
                    conn=conn,
                    user=user,
                    family_id=f"fam_{uuid4().hex}",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    now=now,
                )
                conn.commit()
                return bundle

    def create_user_session_bundle_with_conn(
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
        access_expires_at = now + timedelta(seconds=self._service.session_access_token_ttl_seconds)
        refresh_expires_at = now + timedelta(seconds=self._service.session_refresh_token_ttl_seconds)
        access_token = self.encode_session_token(
            user_id=user.user_id,
            session_id=session_id,
            token_type="access",
            expires_at=access_expires_at,
        )
        refresh_token = self.encode_session_token(
            user_id=user.user_id,
            session_id=session_id,
            token_type="refresh",
            expires_at=refresh_expires_at,
        )
        refresh_hash = self.hash_refresh_token(refresh_token)
        self._service._execute(
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
        return self._service._session_token_bundle_factory(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires_at.isoformat(),
            refresh_expires_at=refresh_expires_at.isoformat(),
        )

    def revoke_session_family_with_conn(self, conn, *, family_id: str, reason: str, now_iso: str) -> None:
        normalized_family = str(family_id or "").strip()
        if not normalized_family:
            return
        self._service._execute(
            conn,
            """
            UPDATE user_sessions
            SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = COALESCE(revoke_reason, ?)
            WHERE refresh_token_family = ?
            """,
            (now_iso, reason, normalized_family),
        )

    def get_registered_user_by_id(self, *, user_id: str):
        with self._service._lock:
            with self._service._connect() as conn:
                return self.get_registered_user_by_id_with_conn(conn=conn, user_id=user_id)

    def get_registered_user_by_id_with_conn(self, *, conn, user_id: str):
        row = self._service._fetchone(
            conn,
            "SELECT id, name, email, is_admin FROM users WHERE id = ?",
            (user_id,),
        )
        if row is None:
            raise InvalidSessionTokenError
        return self._service._registered_user_factory(
            user_id=str(row["id"]),
            email=str(row["email"]),
            name=str(row["name"] or ""),
            token=self._service._encode_token(str(row["id"])),
            is_admin=self._service._row_is_admin(row),
        )

    def hash_refresh_token(self, refresh_token: str) -> str:
        normalized = str(refresh_token or "").strip()
        if not normalized:
            return ""
        return hmac.new(self._service.token_secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    def encode_session_token(
        self,
        *,
        user_id: str,
        session_id: str,
        token_type: str,
        expires_at: datetime,
    ) -> str:
        payload_obj = {
            "uid": user_id,
            "sid": session_id,
            "typ": token_type,
            "exp": int(expires_at.timestamp()),
            "iat": int(self._service.now_provider().timestamp()),
            "jti": uuid4().hex,
        }
        payload_json = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)
        payload = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")
        signature = hmac.new(self._service.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
        return f"{payload}.{signature}"

    def decode_session_token(self, token: str, *, expected_type: str) -> dict[str, str | int]:
        raw = str(token or "").strip()
        try:
            payload, signature = raw.split(".", 1)
        except ValueError:
            raise InvalidSessionTokenError from None
        expected_signature = hmac.new(self._service.token_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[
            :40
        ]
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
        now_unix = int(self._service.now_provider().timestamp())
        if exp_unix <= now_unix:
            raise InvalidSessionTokenError
        return payload_obj

    def extract_session_id_from_token(self, refresh_token: str) -> str:
        payload = self.decode_session_token(refresh_token, expected_type="refresh")
        session_id = str(payload.get("sid") or "").strip()
        if not session_id:
            raise InvalidSessionTokenError
        return session_id
