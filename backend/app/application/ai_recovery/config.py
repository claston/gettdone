from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class AIRecoveryMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class AIRecoveryConfig:
    """Runtime controls with conservative defaults for the inline deployment."""

    mode: AIRecoveryMode
    model_id: str
    max_pages: int
    timeout_seconds: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> AIRecoveryConfig:
        mode = cls._parse_mode(values.get("AI_RECOVERY_MODE", AIRecoveryMode.OFF.value))
        model_id = str(values.get("AI_RECOVERY_MODEL_ID", "us.amazon.nova-2-lite-v1:0") or "").strip()
        if not model_id:
            raise ValueError("AI_RECOVERY_MODEL_ID must not be empty.")

        max_pages = cls._parse_bounded_int(
            values.get("AI_RECOVERY_MAX_PAGES", "15"),
            variable_name="AI_RECOVERY_MAX_PAGES",
            maximum=15,
        )
        timeout_seconds = cls._parse_bounded_int(
            values.get("AI_RECOVERY_TIMEOUT_SECONDS", "25"),
            variable_name="AI_RECOVERY_TIMEOUT_SECONDS",
            maximum=30,
        )
        return cls(
            mode=mode,
            model_id=model_id,
            max_pages=max_pages,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _parse_mode(value: str) -> AIRecoveryMode:
        try:
            return AIRecoveryMode(str(value or "").strip().lower())
        except ValueError as exc:
            supported = ", ".join(item.value for item in AIRecoveryMode)
            raise ValueError(f"Invalid AI_RECOVERY_MODE; expected one of: {supported}.") from exc

    @staticmethod
    def _parse_bounded_int(value: str, *, variable_name: str, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{variable_name} must be an integer between 1 and {maximum}.") from exc
        if not 1 <= parsed <= maximum:
            raise ValueError(f"{variable_name} must be between 1 and {maximum}.")
        return parsed
