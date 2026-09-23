from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping

from app.application.errors import InvalidSessionTokenError, InvalidUserTokenError

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class AsyncConversionRolloutPolicy:
    allowed_user_emails: frozenset[str]
    percentage_rollout_enabled: bool = False
    rollout_percentage: int = 0

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> AsyncConversionRolloutPolicy:
        raw = values.get("CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST", "")
        emails = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
        if any(_EMAIL_PATTERN.fullmatch(email) is None for email in emails):
            raise ValueError("CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST must contain valid email addresses.")
        percentage_rollout_enabled = _parse_bool(
            values.get("CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED", "false"),
            "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED",
        )
        try:
            rollout_percentage = int(values.get("CONVERSION_ASYNC_ROLLOUT_PERCENTAGE", "0"))
        except (TypeError, ValueError) as exc:
            raise ValueError("CONVERSION_ASYNC_ROLLOUT_PERCENTAGE must be an integer between 0 and 100.") from exc
        if not 0 <= rollout_percentage <= 100:
            raise ValueError("CONVERSION_ASYNC_ROLLOUT_PERCENTAGE must be between 0 and 100.")
        return cls(
            allowed_user_emails=emails,
            percentage_rollout_enabled=percentage_rollout_enabled,
            rollout_percentage=rollout_percentage,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.allowed_user_emails) or (
            self.percentage_rollout_enabled and self.rollout_percentage > 0
        )

    def allows(self, *, identity, access_control_service) -> bool:
        return self.batch_max_files(
            identity=identity,
            access_control_service=access_control_service,
            configured_max=1,
        ) is not None

    def batch_max_files(self, *, identity, access_control_service, configured_max: int) -> int | None:
        if not self.enabled or identity is None or identity.identity_type != "user":
            return None
        if self.allowed_user_emails:
            try:
                user = access_control_service.get_user_by_id(identity.identity_id)
            except (InvalidSessionTokenError, InvalidUserTokenError):
                return None
            if user.email.strip().lower() in self.allowed_user_emails:
                return max(1, int(configured_max))
        if not self.percentage_rollout_enabled or self.rollout_percentage <= 0:
            return None
        bucket = int.from_bytes(
            sha256(f"user:{identity.identity_id}".encode("utf-8")).digest()[:8],
            byteorder="big",
        ) % 10_000
        return 1 if bucket < self.rollout_percentage * 100 else None


def _parse_bool(raw: str, variable_name: str) -> bool:
    normalized = str(raw or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{variable_name} must be true or false.")
