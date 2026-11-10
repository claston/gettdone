from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class InvalidPasswordResetTokenError(Exception):
    pass


@dataclass(frozen=True)
class PasswordResetToken:
    token_id: str
    email: str
    token: str


class AccessControlPasswordResetComponent:
    """Persist only token hashes; serialize issuance/reset per user across workers."""

    def __init__(self, service: AccessControlService) -> None:
        self.service = service

    def _lock_user(self, conn, user_id):
        service = self.service
        suffix = " FOR UPDATE" if service._use_postgres else ""
        return service._fetchone(conn, "SELECT * FROM users WHERE id = ?" + suffix, (user_id,))

    def _begin(self, conn):
        if not self.service._use_postgres:
            conn.execute("BEGIN IMMEDIATE")

    def issue(self, *, email: str) -> PasswordResetToken | None:
        service = self.service
        now = service.now_provider()
        with service._lock, service._connect() as conn:
            self._begin(conn)
            row = service._fetchone(conn, "SELECT id FROM users WHERE email = ?", (email.strip().lower(),))
            user = self._lock_user(conn, row["id"]) if row else None
            if user is None or not service._row_is_active(user) or user["auth_provider"] != "local":
                conn.rollback()
                return None
            recent = service._fetchone(conn, """
                SELECT COUNT(*) AS total, MAX(created_at) AS latest FROM password_reset_tokens
                WHERE user_id = ? AND created_at >= ?
            """, (user["id"], (now - timedelta(days=1)).isoformat()))
            if recent["total"] >= 5 or (recent["latest"] and
                    service._parse_usage_datetime(recent["latest"], fallback=now) > now - timedelta(seconds=60)):
                conn.rollback()
                return None
            service._execute(conn, """
                UPDATE password_reset_tokens SET invalidated_at = ?
                WHERE user_id = ? AND consumed_at IS NULL AND invalidated_at IS NULL
            """, (now.isoformat(), user["id"]))
            token = secrets.token_urlsafe(32)
            token_id = f"prt_{uuid4().hex}"
            service._execute(conn, """
                INSERT INTO password_reset_tokens (id, user_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (token_id, user["id"], hashlib.sha256(token.encode()).hexdigest(),
                  now.isoformat(), (now + timedelta(minutes=30)).isoformat()))
            conn.commit()
        return PasswordResetToken(token_id=token_id, email=user["email"], token=token)

    def mark_delivery(self, *, token_id: str, sent: bool):
        service = self.service
        with service._lock, service._connect() as conn:
            service._execute(conn, """
                UPDATE password_reset_tokens SET delivery_status = ?, invalidated_at = COALESCE(invalidated_at, ?)
                WHERE id = ?
            """, ("sent" if sent else "failed", None if sent else service.now_provider().isoformat(), token_id))
            conn.commit()

    def confirm(self, *, token: str, password: str):
        if not 8 <= len(password) <= 256:
            raise ValueError("Password must contain 8 to 256 characters")
        service = self.service
        now = service.now_provider()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with service._lock, service._connect() as conn:
            self._begin(conn)
            try:
                row = service._fetchone(conn, "SELECT * FROM password_reset_tokens WHERE token_hash = ?", (token_hash,))
                if row is None:
                    raise InvalidPasswordResetTokenError
                user = self._lock_user(conn, row["user_id"])
                # Re-read after locking: another process may have consumed or superseded this link.
                row = service._fetchone(conn, "SELECT * FROM password_reset_tokens WHERE token_hash = ?", (token_hash,))
                if (row["consumed_at"] or row["invalidated_at"] or not service._row_is_active(user)
                        or user["auth_provider"] != "local"
                        or service._parse_usage_datetime(row["expires_at"], fallback=now) <= now):
                    raise InvalidPasswordResetTokenError
                salt = secrets.token_hex(16)
                password_hash = service._hash_password(password=password, salt=salt)
                service._execute(conn, """
                    UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ?,
                        credential_version = credential_version + 1 WHERE id = ?
                """, (password_hash, salt, now.isoformat(), user["id"]))
                service._execute(conn, "UPDATE password_reset_tokens SET consumed_at = ? WHERE id = ?",
                                 (now.isoformat(), row["id"]))
                service._execute(conn, """
                    UPDATE password_reset_tokens SET invalidated_at = ?
                    WHERE user_id = ? AND consumed_at IS NULL AND invalidated_at IS NULL
                """, (now.isoformat(), user["id"]))
                service._execute(conn, """
                    UPDATE user_sessions SET revoked_at = ?, revoke_reason = 'password_reset'
                    WHERE user_id = ? AND revoked_at IS NULL
                """, (now.isoformat(), user["id"]))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
