from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidUserTokenError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


logger = logging.getLogger(__name__)


class AccessControlAdminComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    @staticmethod
    def normalize_admin_emails(emails: set[str] | None) -> set[str]:
        if not emails:
            return set()
        normalized: set[str] = set()
        for email in emails:
            value = str(email or "").strip().lower()
            if value:
                normalized.add(value)
        return normalized

    @staticmethod
    def row_bool_from_value(raw) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        return str(raw or "").strip().lower() in {"1", "true", "t", "yes"}

    def row_is_admin(self, row) -> bool:
        if row is None:
            return False
        keys = row.keys() if hasattr(row, "keys") else ()
        if "is_admin" not in keys:
            return False
        return self.row_bool_from_value(row["is_admin"])

    def row_is_active(self, row) -> bool:
        if row is None:
            return False
        keys = row.keys() if hasattr(row, "keys") else ()
        if "is_active" not in keys:
            return True
        return self.row_bool_from_value(row["is_active"])

    def sync_admin_emails(self, conn) -> None:
        if not self._service.admin_emails:
            return
        for email in self._service.admin_emails:
            self._service._execute(
                conn,
                "UPDATE users SET is_admin = ? WHERE lower(email) = ?",
                (self._service._true_value(), email),
            )

    def is_user_admin(self, *, user_id: str) -> bool:
        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
                    conn,
                    "SELECT is_admin, is_active FROM users WHERE id = ?",
                    (user_id,),
                )
                if row is None or not self.row_is_active(row):
                    raise InvalidUserTokenError
                return self.row_is_admin(row)

    def list_users_for_admin(
        self,
        *,
        query: str | None = None,
        only_admin: bool | None = None,
        only_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | bool | int | None]], int]:
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip().lower()

        with self._service._lock:
            with self._service._connect() as conn:
                where: list[str] = []
                params: list[str | int] = []
                if only_admin is True:
                    where.append("users.is_admin = ?")
                    params.append(self._service._true_value())
                elif only_admin is False:
                    where.append("users.is_admin = ?")
                    params.append(self._service._false_value())
                if only_active is True:
                    where.append("users.is_active = ?")
                    params.append(self._service._true_value())
                elif only_active is False:
                    where.append("users.is_active = ?")
                    params.append(self._service._false_value())
                if normalized_query:
                    where.append(
                        "(lower(users.name) LIKE ? OR lower(users.email) LIKE ? OR lower(users.id) LIKE ?)"
                    )
                    like = f"%{normalized_query}%"
                    params.extend([like, like, like])

                base = "FROM users"
                if where:
                    base += " WHERE " + " AND ".join(where)

                total_row = self._service._fetchone(conn, f"SELECT COUNT(1) AS total {base}", tuple(params))
                total = int(total_row["total"]) if total_row is not None else 0
                rows = self._service._fetchall(
                    conn,
                    f"""
                    SELECT
                        users.id,
                        users.name,
                        users.email,
                        users.is_admin,
                        users.is_active,
                        users.email_verification_status,
                        users.email_verified_at,
                        users.created_at,
                        users.updated_at
                    {base}
                    ORDER BY users.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params + [normalized_limit, normalized_offset]),
                )
                login_stats_by_user_id: dict[str, dict[str, int | str | None]] = {}
                user_ids = [str(row["id"]) for row in rows]
                if user_ids:
                    placeholders = ", ".join("?" for _ in user_ids)
                    stats_rows = self._service._fetchall(
                        conn,
                        f"""
                        SELECT
                            user_id,
                            COUNT(1) AS login_count,
                            SUM(CASE WHEN auth_method = 'local_password' THEN 1 ELSE 0 END) AS local_login_count,
                            SUM(CASE WHEN auth_method = 'google_oauth' THEN 1 ELSE 0 END) AS google_login_count,
                            MAX(created_at) AS last_login_at
                        FROM user_login_events
                        WHERE user_id IN ({placeholders})
                        GROUP BY user_id
                        """,
                        tuple(user_ids),
                    )
                    login_stats_by_user_id = {
                        str(stats_row["user_id"]): {
                            "login_count": int(stats_row["login_count"] or 0),
                            "local_login_count": int(stats_row["local_login_count"] or 0),
                            "google_login_count": int(stats_row["google_login_count"] or 0),
                            "last_login_at": str(stats_row["last_login_at"] or "") or None,
                        }
                        for stats_row in stats_rows
                    }
                items: list[dict[str, str | bool | int | None]] = []
                for row in rows:
                    login_stats = login_stats_by_user_id.get(str(row["id"]), {})
                    items.append(
                        {
                            "user_id": str(row["id"]),
                            "name": str(row["name"] or ""),
                            "email": str(row["email"] or ""),
                            "is_admin": self.row_is_admin(row),
                            "is_active": self.row_is_active(row),
                            "email_verification_status": str(
                                row["email_verification_status"] or "verified"
                            ),
                            "email_verified_at": str(row["email_verified_at"] or "") or None,
                            "created_at": str(row["created_at"] or ""),
                            "updated_at": str(row["updated_at"] or ""),
                            "login_count": int(login_stats.get("login_count") or 0),
                            "local_login_count": int(login_stats.get("local_login_count") or 0),
                            "google_login_count": int(login_stats.get("google_login_count") or 0),
                            "last_login_at": login_stats.get("last_login_at"),
                        }
                    )
                return items, total

    def list_marketing_contacts_for_admin(
        self,
        *,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | bool | None]], int]:
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip().lower()

        with self._service._lock:
            with self._service._connect() as conn:
                where = ["users.product_updates_opt_in = ?"]
                params: list[str | int] = [self._service._true_value()]
                if normalized_query:
                    where.append("(lower(users.name) LIKE ? OR lower(users.email) LIKE ?)")
                    like = f"%{normalized_query}%"
                    params.extend([like, like])

                base = "FROM users WHERE " + " AND ".join(where)
                total_row = self._service._fetchone(
                    conn,
                    f"SELECT COUNT(1) AS total {base}",
                    tuple(params),
                )
                total = int(total_row["total"]) if total_row is not None else 0
                rows = self._service._fetchall(
                    conn,
                    f"""
                    SELECT
                        users.id,
                        users.name,
                        users.email,
                        users.is_active,
                        users.email_verification_status,
                        users.product_updates_opted_in_at,
                        users.created_at
                    {base}
                    ORDER BY
                        users.product_updates_opted_in_at DESC,
                        users.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params + [normalized_limit, normalized_offset]),
                )
                return (
                    [
                        {
                            "user_id": str(row["id"]),
                            "name": str(row["name"] or ""),
                            "email": str(row["email"] or ""),
                            "is_active": self.row_is_active(row),
                            "email_verification_status": str(
                                row["email_verification_status"] or "verified"
                            ),
                            "product_updates_opted_in_at": str(
                                row["product_updates_opted_in_at"] or ""
                            )
                            or None,
                            "created_at": str(row["created_at"] or ""),
                        }
                        for row in rows
                    ],
                    total,
                )

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
                    """
                    SELECT id, name, email, is_admin, is_active, email_verification_status,
                           email_verified_at, created_at, updated_at
                    FROM users WHERE id = ?
                    """,
                    (normalized_user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                previous_is_admin = self.row_is_admin(row)
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
                    "is_active": self.row_is_active(row),
                    "email_verification_status": str(row["email_verification_status"] or "verified"),
                    "email_verified_at": str(row["email_verified_at"] or "") or None,
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": now_iso,
                }

    def set_user_active_status(self, *, user_id: str, is_active: bool) -> dict[str, str | bool]:
        return self.set_user_active_status_with_actor(
            user_id=user_id,
            is_active=is_active,
            actor_user_id=None,
        )

    def set_user_active_status_with_actor(
        self,
        *,
        user_id: str,
        is_active: bool,
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
                    """
                    SELECT id, name, email, is_admin, is_active, email_verification_status,
                           email_verified_at, created_at, updated_at
                    FROM users WHERE id = ?
                    """,
                    (normalized_user_id,),
                )
                if row is None:
                    raise InvalidUserTokenError
                previous_is_active = self.row_is_active(row)
                self._service._execute(
                    conn,
                    "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                    (
                        self._service._true_value() if is_active else self._service._false_value(),
                        now_iso,
                        normalized_user_id,
                    ),
                )
                if not is_active:
                    self._service._execute(
                        conn,
                        """
                        UPDATE user_sessions
                        SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = COALESCE(revoke_reason, ?)
                        WHERE user_id = ? AND revoked_at IS NULL
                        """,
                        (now_iso, "user_deactivated", normalized_user_id),
                    )
                conn.commit()
                logger.info(
                    "admin_user_status_changed actor_user_id=%s target_user_id=%s previous_is_active=%s new_is_active=%s",
                    str(actor_user_id or "").strip() or "system",
                    normalized_user_id,
                    previous_is_active,
                    bool(is_active),
                )
                return {
                    "user_id": str(row["id"]),
                    "name": str(row["name"] or ""),
                    "email": str(row["email"] or ""),
                    "is_admin": self.row_is_admin(row),
                    "is_active": bool(is_active),
                    "email_verification_status": str(row["email_verification_status"] or "verified"),
                    "email_verified_at": str(row["email_verified_at"] or "") or None,
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
                            "previous_is_admin": self.row_bool_from_value(row["previous_is_admin"]),
                            "new_is_admin": self.row_bool_from_value(row["new_is_admin"]),
                            "created_at": str(row["created_at"]),
                        }
                    )
                return items
