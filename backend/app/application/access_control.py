import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.application.errors import FileTooLargeError, InvalidUserTokenError, QuotaExceededError, UserAlreadyExistsError

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
        self.token_secret = token_secret.encode("utf-8")
        self.anonymous_quota_limit = anonymous_quota_limit
        self.registered_quota_limit = registered_quota_limit
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def resolve_identity(
        self,
        anonymous_fingerprint: str | None,
        user_token: str | None,
    ) -> IdentityContext:
        if user_token:
            user_id = self._decode_token(user_token)
            if user_id not in self._state["users"]:
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
        with self._lock:
            if normalized_email in self._state["users_by_email"]:
                raise UserAlreadyExistsError
            user_id = f"usr_{uuid4().hex[:12]}"
            salt = secrets.token_hex(8)
            password_hash = self._hash_password(password=password, salt=salt)
            self._state["users"][user_id] = {
                "id": user_id,
                "name": name.strip(),
                "email": normalized_email,
                "password_hash": password_hash,
                "password_salt": salt,
                "created_at": now,
                "updated_at": now,
            }
            self._state["users_by_email"][normalized_email] = user_id
            self._write_state()
        return RegisteredUser(
            user_id=user_id,
            email=normalized_email,
            name=name.strip(),
            token=self._encode_token(user_id),
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
            usage["used_count"] += 1
            usage["updated_at"] = self.now_provider().isoformat()
            self._state["usage"][self._usage_key(identity)] = usage
            self._write_state()
            return identity.quota_limit - usage["used_count"]

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        usage = self._read_usage(identity)
        return max(identity.quota_limit - usage["used_count"], 0)

    def _ensure_anonymous_identity(self, fingerprint: str) -> str:
        now = self.now_provider().isoformat()
        with self._lock:
            existing_id = self._state["anonymous_by_fingerprint"].get(fingerprint)
            if existing_id:
                self._state["anonymous_identities"][existing_id]["last_seen_at"] = now
                self._write_state()
                return existing_id
            anon_id = f"anon_{uuid4().hex[:12]}"
            self._state["anonymous_identities"][anon_id] = {
                "id": anon_id,
                "fingerprint": fingerprint,
                "created_at": now,
                "last_seen_at": now,
            }
            self._state["anonymous_by_fingerprint"][fingerprint] = anon_id
            self._write_state()
            return anon_id

    def _read_usage(self, identity: IdentityContext) -> dict[str, int | str]:
        key = self._usage_key(identity)
        with self._lock:
            usage = self._state["usage"].get(key)
            if usage is None:
                usage = {
                    "identity_type": identity.identity_type,
                    "identity_id": identity.identity_id,
                    "used_count": 0,
                    "quota_limit": identity.quota_limit,
                    "updated_at": self.now_provider().isoformat(),
                }
                self._state["usage"][key] = usage
                self._write_state()
            return dict(usage)

    def _usage_key(self, identity: IdentityContext) -> str:
        return f"{identity.identity_type}:{identity.identity_id}"

    def _hash_password(self, password: str, salt: str) -> str:
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PASSWORD_HASH_ITERATIONS,
        )
        return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${base64.b64encode(derived_key).decode('ascii')}"

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

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            return {
                "users": {},
                "users_by_email": {},
                "anonymous_identities": {},
                "anonymous_by_fingerprint": {},
                "usage": {},
            }
        try:
            content = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {
                "users": content.get("users", {}),
                "users_by_email": content.get("users_by_email", {}),
                "anonymous_identities": content.get("anonymous_identities", {}),
                "anonymous_by_fingerprint": content.get("anonymous_by_fingerprint", {}),
                "usage": content.get("usage", {}),
            }
        except (OSError, json.JSONDecodeError):
            return {
                "users": {},
                "users_by_email": {},
                "anonymous_identities": {},
                "anonymous_by_fingerprint": {},
                "usage": {},
            }

    def _write_state(self) -> None:
        self.state_file.write_text(json.dumps(self._state, ensure_ascii=True, indent=2), encoding="utf-8")
