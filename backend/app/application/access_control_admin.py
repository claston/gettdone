from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidUserTokenError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class AccessControlAdminComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def is_user_admin(self, *, user_id: str) -> bool:
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    "SELECT is_admin FROM users WHERE id = ?",
                    (user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                return self._service._row_is_admin(row)

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

        with self._service._lock:
            with self._service._connect() as conn:
                where: list[str] = []
                params: list[str | int] = []
                if only_admin is True:
                    where.append("is_admin = ?")
                    params.append(self._service._true_value())
                elif only_admin is False:
                    where.append("is_admin = ?")
                    params.append(self._service._false_value())
                if normalized_query:
                    where.append("(lower(name) LIKE ? OR lower(email) LIKE ? OR lower(id) LIKE ?)")
                    like = f"%{normalized_query}%"
                    params.extend([like, like, like])

                base = "FROM users"
                if where:
                    base += " WHERE " + " AND ".join(where)

                total_row = self._service._fetchone(conn, f"SELECT COUNT(1) AS total {base}", tuple(params))
                total = int(total_row["total"]) if total_row is not None else 0
                rows = self._service._fetchall(
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
                            "is_admin": self._service._row_is_admin(row),
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
        now_iso = self._service.now_provider().isoformat()

        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin, created_at, updated_at FROM users WHERE id = ?",
                    (normalized_user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                previous_is_admin = self._service._row_is_admin(row)
                self._service._execute(
                    conn,
                    "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
                    (self._service._true_value() if is_admin else self._service._false_value(), now_iso, normalized_user_id),
                )
                actor_email: str | None = None
                if actor_user_id:
                    actor_row = self._service._fetchone(
                        conn,
                        "SELECT email FROM users WHERE id = ?",
                        (str(actor_user_id).strip(),),
                    )
                    if actor_row is not None:
                        actor_email = str(actor_row["email"] or "") or None

                event_type = "ADMIN_ROLE_GRANTED" if is_admin else "ADMIN_ROLE_REVOKED"
                self._service._execute(
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
                        self._service._true_value() if previous_is_admin else self._service._false_value(),
                        self._service._true_value() if is_admin else self._service._false_value(),
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
        with self._service._lock:
            with self._service._connect() as conn:
                rows = self._service._fetchall(
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
                            "previous_is_admin": self._service._row_bool_from_value(row["previous_is_admin"]),
                            "new_is_admin": self._service._row_bool_from_value(row["new_is_admin"]),
                            "created_at": str(row["created_at"]),
                        }
                    )
                return items
