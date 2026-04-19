import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.application.errors import (
    FileTooLargeError,
    InvalidCredentialsError,
    InvalidUserTokenError,
    QuotaExceededError,
    UserAlreadyExistsError,
)

ANONYMOUS_QUOTA_LIMIT = 3
REGISTERED_QUOTA_LIMIT = 10
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024
PASSWORD_HASH_ITERATIONS = 390_000


@dataclass(frozen=True)
class IdentityContext:
    identity_type: str
    identity_id: str
    quota_limit: int


@dataclass(frozen=True)
class RegisteredUser:
    user_id: str
    email: str
    name: str
    token: str


class AccessControlService:
    def __init__(
        self,
        state_file: Path,
        token_secret: str,
        anonymous_quota_limit: int = ANONYMOUS_QUOTA_LIMIT,
        registered_quota_limit: int = REGISTERED_QUOTA_LIMIT,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.state_file = state_file
        self.db_file = state_file.with_suffix(".db")
        self.token_secret = token_secret.encode("utf-8")
        self.anonymous_quota_limit = anonymous_quota_limit
        self.registered_quota_limit = registered_quota_limit
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
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
            return IdentityContext(
                identity_type="user",
                identity_id=user_id,
                quota_limit=self.registered_quota_limit,
            )

        fingerprint = (anonymous_fingerprint or "").strip()
        if not fingerprint:
            raise InvalidUserTokenError
        anon_id = self._ensure_anonymous_identity(fingerprint)
        return IdentityContext(
            identity_type="anonymous",
            identity_id=anon_id,
            quota_limit=self.anonymous_quota_limit,
        )

    def register_user(self, name: str, email: str, password: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        now = self.now_provider().isoformat()
        user_id = f"usr_{uuid4().hex[:12]}"
        salt = secrets.token_hex(8)
        password_hash = self._hash_password(password=password, salt=salt)
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone()
                if existing is not None:
                    raise UserAlreadyExistsError
                conn.execute(
                    """
                    INSERT INTO users (id, name, email, password_hash, password_salt, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, name.strip(), normalized_email, password_hash, salt, now, now),
                )
                conn.commit()
        return RegisteredUser(
            user_id=user_id,
            email=normalized_email,
            name=name.strip(),
            token=self._encode_token(user_id),
        )

    def authenticate_user(self, email: str, password: str) -> RegisteredUser:
        normalized_email = email.strip().lower()
        with self._lock:
            with self._connect() as conn:
                user = conn.execute(
                    "SELECT id, name, email, password_hash, password_salt FROM users WHERE email = ?",
                    (normalized_email,),
                ).fetchone()
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
                )

    def get_user_by_token(self, user_token: str) -> RegisteredUser:
        user_id = self._decode_token(user_token)
        with self._lock:
            with self._connect() as conn:
                user = conn.execute(
                    "SELECT id, name, email FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if user is None:
                    raise InvalidUserTokenError
                return RegisteredUser(
                    user_id=str(user["id"]),
                    email=str(user["email"]),
                    name=str(user["name"] or ""),
                    token=user_token,
                )

    def assert_upload_size(self, raw_bytes: bytes) -> None:
        if len(raw_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise FileTooLargeError

    def ensure_quota_available(self, identity: IdentityContext) -> None:
        usage = self._read_usage(identity)
        if usage["used_count"] >= identity.quota_limit:
            raise QuotaExceededError

    def consume_quota(self, identity: IdentityContext) -> int:
        with self._lock:
            usage = self._read_usage(identity)
            if usage["used_count"] >= identity.quota_limit:
                raise QuotaExceededError
            used_count = usage["used_count"] + 1
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO usage (identity_type, identity_id, used_count, quota_limit, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(identity_type, identity_id)
                    DO UPDATE SET
                      used_count=excluded.used_count,
                      quota_limit=excluded.quota_limit,
                      updated_at=excluded.updated_at
                    """,
                    (
                        identity.identity_type,
                        identity.identity_id,
                        used_count,
                        identity.quota_limit,
                        self.now_provider().isoformat(),
                    ),
                )
                conn.commit()
            return identity.quota_limit - used_count

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        usage = self._read_usage(identity)
        return max(identity.quota_limit - usage["used_count"], 0)

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
        created_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO user_conversions (
                      analysis_id,
                      user_id,
                      created_at,
                      expires_at,
                      filename,
                      model,
                      conversion_type,
                      status,
                      transactions_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(analysis_id)
                    DO UPDATE SET
                      user_id=excluded.user_id,
                      created_at=excluded.created_at,
                      expires_at=excluded.expires_at,
                      filename=excluded.filename,
                      model=excluded.model,
                      conversion_type=excluded.conversion_type,
                      status=excluded.status,
                      transactions_count=excluded.transactions_count
                    """,
                    (
                        processing_id,
                        user_id,
                        created_at or self.now_provider().isoformat(),
                        expires_at,
                        filename.strip() or f"{processing_id}.pdf",
                        model.strip() or "Nao identificado",
                        conversion_type.strip() or "pdf-ofx",
                        status.strip() or "Sucesso",
                        transactions_count,
                    ),
                )
                conn.commit()

    def list_user_conversions(self, *, user_id: str, limit: int = 20) -> list[dict[str, str | int]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT analysis_id, created_at, expires_at, filename, model, conversion_type, status, transactions_count
                    FROM user_conversions
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, max(1, min(limit, 100))),
                ).fetchall()

        now = self.now_provider()
        items: list[dict[str, str | int]] = []
        for row in rows:
            status = str(row["status"] or "Sucesso")
            expires_at = str(row["expires_at"] or "").strip()
            if expires_at and self._is_expired(expires_at, now):
                status = "Expirado"
            item: dict[str, str | int] = {
                "processing_id": str(row["analysis_id"]),
                "created_at": str(row["created_at"]),
                "filename": str(row["filename"]),
                "model": str(row["model"]),
                "conversion_type": str(row["conversion_type"]),
                "status": status,
            }
            tx_count = row["transactions_count"]
            if isinstance(tx_count, int):
                item["transactions_count"] = tx_count
            items.append(item)
        return items

    def _ensure_anonymous_identity(self, fingerprint: str) -> str:
        now = self.now_provider().isoformat()
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT id FROM anonymous_identities WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if existing is not None:
                    anon_id = str(existing["id"])
                    conn.execute(
                        "UPDATE anonymous_identities SET last_seen_at = ? WHERE id = ?",
                        (now, anon_id),
                    )
                    conn.commit()
                    return anon_id
                anon_id = f"anon_{uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO anonymous_identities (id, fingerprint, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (anon_id, fingerprint, now, now),
                )
                conn.commit()
                return anon_id

    def _read_usage(self, identity: IdentityContext) -> dict[str, int]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT used_count FROM usage WHERE identity_type = ? AND identity_id = ?",
                    (identity.identity_type, identity.identity_id),
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO usage (identity_type, identity_id, used_count, quota_limit, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            identity.identity_type,
                            identity.identity_id,
                            0,
                            identity.quota_limit,
                            self.now_provider().isoformat(),
                        ),
                    )
                    conn.commit()
                    return {"used_count": 0}
                return {"used_count": int(row["used_count"] or 0)}

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
                row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
                return row is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
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
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    );
                    """
                )
                conn.commit()

    def _is_expired(self, expires_at_raw: str, now: datetime) -> bool:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at < now
