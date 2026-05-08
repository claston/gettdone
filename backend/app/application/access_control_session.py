from __future__ import annotations

import hmac
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidSessionTokenError, ReusedSessionTokenError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, SessionTokenBundle


class AccessControlSessionComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def create_user_session(
        self,
        *,
        user_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionTokenBundle:
        user = self._service._get_registered_user_by_id(user_id=user_id)
        return self._service._create_user_session_bundle(user=user, ip_address=ip_address, user_agent=user_agent)

    def refresh_user_session(
        self,
        *,
        refresh_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionTokenBundle:
        payload = self._service._decode_session_token(refresh_token, expected_type="refresh")
        session_id = str(payload.get("sid") or "").strip()
        user_id = str(payload.get("uid") or "").strip()
        if not session_id or not user_id:
            raise InvalidSessionTokenError
        now = self._service.now_provider()
        refresh_hash = self._service._hash_refresh_token(refresh_token)
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    """
                    SELECT id, user_id, refresh_token_hash, refresh_token_family, expires_at, revoked_at, rotated_at
                    FROM user_sessions
                    WHERE id = ?
                    """,
                    (session_id,),
                )
                if row is None:
                    raise InvalidSessionTokenError
                if str(row["user_id"] or "").strip() != user_id:
                    raise InvalidSessionTokenError
                expected_hash = str(row["refresh_token_hash"] or "").strip()
                if not hmac.compare_digest(expected_hash, refresh_hash):
                    self._service._revoke_session_family_with_conn(
                        conn,
                        family_id=str(row["refresh_token_family"] or "").strip(),
                        reason="refresh_hash_mismatch",
                        now_iso=now.isoformat(),
                    )
                    conn.commit()
                    raise ReusedSessionTokenError
                if str(row["revoked_at"] or "").strip():
                    raise InvalidSessionTokenError
                if str(row["rotated_at"] or "").strip():
                    self._service._revoke_session_family_with_conn(
                        conn,
                        family_id=str(row["refresh_token_family"] or "").strip(),
                        reason="refresh_token_reuse",
                        now_iso=now.isoformat(),
                    )
                    conn.commit()
                    raise ReusedSessionTokenError

                expires_at = self._service._parse_usage_datetime(str(row["expires_at"] or ""), fallback=now)
                if expires_at <= now:
                    self._service._execute(
                        conn,
                        "UPDATE user_sessions SET revoked_at = ?, revoke_reason = ? WHERE id = ?",
                        (now.isoformat(), "refresh_expired", session_id),
                    )
                    conn.commit()
                    raise InvalidSessionTokenError

                user = self._service._get_registered_user_by_id_with_conn(conn=conn, user_id=user_id)
                family_id = str(row["refresh_token_family"] or "").strip() or f"fam_{uuid4().hex}"
                bundle = self._service._create_user_session_bundle_with_conn(
                    conn=conn,
                    user=user,
                    family_id=family_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    now=now,
                )
                self._service._execute(
                    conn,
                    """
                    UPDATE user_sessions
                    SET rotated_at = ?, replaced_by_session_id = ?, last_ip = ?, last_user_agent = ?
                    WHERE id = ?
                    """,
                    (
                        now.isoformat(),
                        self._service._extract_session_id_from_token(bundle.refresh_token),
                        (ip_address or "").strip() or None,
                        (user_agent or "").strip()[:512] or None,
                        session_id,
                    ),
                )
                conn.commit()
                return bundle

    def revoke_user_session(self, *, refresh_token: str) -> None:
        try:
            payload = self._service._decode_session_token(refresh_token, expected_type="refresh")
        except InvalidSessionTokenError:
            return
        session_id = str(payload.get("sid") or "").strip()
        if not session_id:
            return
        now_iso = self._service.now_provider().isoformat()
        refresh_hash = self._service._hash_refresh_token(refresh_token)
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    "SELECT refresh_token_hash FROM user_sessions WHERE id = ?",
                    (session_id,),
                )
                if row is None:
                    return
                expected_hash = str(row["refresh_token_hash"] or "").strip()
                if not hmac.compare_digest(expected_hash, refresh_hash):
                    return
                self._service._execute(
                    conn,
                    "UPDATE user_sessions SET revoked_at = ?, revoke_reason = ? WHERE id = ? AND revoked_at IS NULL",
                    (now_iso, "logout", session_id),
                )
                conn.commit()

    def revoke_all_user_sessions(self, *, user_id: str) -> None:
        now_iso = self._service.now_provider().isoformat()
        with self._service._lock:
            with self._service._connect() as conn:
                self._service._execute(
                    conn,
                    """
                    UPDATE user_sessions
                    SET revoked_at = ?, revoke_reason = ?
                    WHERE user_id = ? AND revoked_at IS NULL
                    """,
                    (now_iso, "logout_all", user_id),
                )
                conn.commit()

    def get_user_by_session_access_token(self, access_token: str):
        payload = self._service._decode_session_token(access_token, expected_type="access")
        session_id = str(payload.get("sid") or "").strip()
        user_id = str(payload.get("uid") or "").strip()
        if not session_id or not user_id:
            raise InvalidSessionTokenError
        now = self._service.now_provider()
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    """
                    SELECT revoked_at, expires_at
                    FROM user_sessions
                    WHERE id = ? AND user_id = ?
                    """,
                    (session_id, user_id),
                )
                if row is None:
                    raise InvalidSessionTokenError
                if str(row["revoked_at"] or "").strip():
                    raise InvalidSessionTokenError
                refresh_expires_at = self._service._parse_usage_datetime(str(row["expires_at"] or ""), fallback=now)
                if refresh_expires_at <= now:
                    raise InvalidSessionTokenError
                return self._service._get_registered_user_by_id_with_conn(conn=conn, user_id=user_id)
