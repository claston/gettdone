from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.errors import InvalidCredentialsError, InvalidUserTokenError, UserAlreadyExistsError

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, RegisteredUser


class AccessControlAuthComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def register_user(
        self,
        name: str,
        email: str,
        password: str,
        *,
        terms_accepted_at: str | None = None,
        privacy_accepted_at: str | None = None,
        product_updates_opt_in: bool = False,
        product_updates_opted_in_at: str | None = None,
    ) -> RegisteredUser:
        normalized_email = email.strip().lower()
        is_admin = normalized_email in self._service.admin_emails
        now = self._service.now_provider().isoformat()
        user_id = f"usr_{uuid4().hex[:12]}"
        salt = secrets.token_hex(8)
        password_hash = self._service._hash_password(password=password, salt=salt)
        with self._service._lock:
            with self._service._connect() as conn:
                existing = self._service._fetchone(conn, "SELECT id FROM users WHERE email = ?", (normalized_email,))
                if existing is not None:
                    raise UserAlreadyExistsError
                self._service._execute(
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
                        terms_accepted_at,
                        privacy_accepted_at,
                        product_updates_opt_in,
                        product_updates_opted_in_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        terms_accepted_at,
                        privacy_accepted_at,
                        self._service._true_value() if product_updates_opt_in else self._service._false_value(),
                        product_updates_opted_in_at,
                        now,
                        now,
                    ),
                )
                conn.commit()
        return self._service._registered_user_factory(
            user_id=user_id,
            email=normalized_email,
            name=name.strip(),
            token=self._service._encode_token(user_id),
            is_admin=is_admin,
        )

    def authenticate_user(self, email: str, password: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        with self._service._lock:
            with self._service._connect() as conn:
                user = self._service._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin, password_hash, password_salt FROM users WHERE email = ?",
                    (normalized_email,),
                )
                if user is None:
                    raise InvalidCredentialsError
                if not self._service._verify_password(
                    password=password,
                    stored_hash=str(user["password_hash"] or ""),
                    stored_salt=str(user["password_salt"] or ""),
                ):
                    raise InvalidCredentialsError
                return self._service._registered_user_factory(
                    user_id=str(user["id"]),
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=self._service._encode_token(str(user["id"])),
                    is_admin=self._service._row_is_admin(user),
                )

    def get_user_by_token(self, user_token: str) -> RegisteredUser:
        user_id = self._service._decode_token(user_token)
        with self._service._lock:
            with self._service._connect() as conn:
                user = self._service._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE id = ?",
                    (user_id,),
                )
                if user is None:
                    raise InvalidUserTokenError
                return self._service._registered_user_factory(
                    user_id=str(user["id"]),
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=user_token,
                    is_admin=self._service._row_is_admin(user),
                )

    def get_user_by_email(self, email: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        with self._service._lock:
            with self._service._connect() as conn:
                user = self._service._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE lower(email) = ?",
                    (normalized_email,),
                )
                if user is None:
                    raise InvalidUserTokenError
                user_id = str(user["id"])
                return self._service._registered_user_factory(
                    user_id=user_id,
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=self._service._encode_token(user_id),
                    is_admin=self._service._row_is_admin(user),
                )

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
        now = self._service.now_provider().isoformat()

        with self._service._lock:
            with self._service._connect() as conn:
                row = self._service._fetchone(
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
                    self._service._execute(
                        conn,
                        """
                        UPDATE users
                        SET name = ?, email = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (display_name, normalized_email, now, user_id),
                    )
                    conn.commit()
                    return self._service._registered_user_factory(
                        user_id=user_id,
                        email=normalized_email,
                        name=display_name,
                        token=self._service._encode_token(user_id),
                        is_admin=self._service._row_is_admin(row),
                    )

                existing_by_email = self._service._fetchone(
                    conn,
                    "SELECT id, name, email, is_admin FROM users WHERE email = ?",
                    (normalized_email,),
                )
                if existing_by_email is not None:
                    user_id = str(existing_by_email["id"])
                    self._service._execute(
                        conn,
                        """
                        UPDATE users
                        SET name = ?, auth_provider = 'google', provider_user_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (display_name, provider_user_id, now, user_id),
                    )
                    conn.commit()
                    return self._service._registered_user_factory(
                        user_id=user_id,
                        email=normalized_email,
                        name=display_name,
                        token=self._service._encode_token(user_id),
                        is_admin=self._service._row_is_admin(existing_by_email),
                    )

                user_id = f"usr_{uuid4().hex[:12]}"
                is_admin = normalized_email in self._service.admin_emails
                self._service._execute(
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
                return self._service._registered_user_factory(
                    user_id=user_id,
                    email=normalized_email,
                    name=display_name,
                    token=self._service._encode_token(user_id),
                    is_admin=is_admin,
                )
