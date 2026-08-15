from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


LOGIN_AUTH_METHODS = frozenset({"local_password", "google_oauth"})


class AccessControlLoginEventsComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def record_successful_login(self, *, user_id: str, auth_method: str) -> str:
        normalized_user_id = str(user_id or "").strip()
        normalized_auth_method = str(auth_method or "").strip().lower()
        if not normalized_user_id:
            raise ValueError("user_id is required")
        if normalized_auth_method not in LOGIN_AUTH_METHODS:
            raise ValueError("Unsupported login auth_method")

        event_id = f"ule_{uuid4().hex[:20]}"
        created_at = self._service.now_provider().isoformat()
        with self._service._lock:
            with self._service._connect() as conn:
                self._service._execute(
                    conn,
                    """
                    INSERT INTO user_login_events (id, user_id, auth_method, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, normalized_user_id, normalized_auth_method, created_at),
                )
                conn.commit()
        return event_id

    def list_user_login_events_for_admin(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, str]]:
        normalized_user_id = str(user_id or "").strip()
        normalized_limit = max(1, min(int(limit), 500))
        if not normalized_user_id:
            return []

        with self._service._lock:
            with self._service._connect() as conn:
                rows = self._service._fetchall(
                    conn,
                    """
                    SELECT id, user_id, auth_method, created_at
                    FROM user_login_events
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (normalized_user_id, normalized_limit),
                )
        return [
            {
                "event_id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "auth_method": str(row["auth_method"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
