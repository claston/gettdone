from dataclasses import dataclass
from enum import Enum
from typing import Any


class ConversionPipelineStatus(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConversionPipelineResult:
    status: ConversionPipelineStatus
    payload: dict[str, Any] | None = None
    rejection_reason: str | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def completed(
        cls,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> "ConversionPipelineResult":
        return cls(
            status=ConversionPipelineStatus.COMPLETED,
            payload=dict(payload),
            metadata=dict(metadata) if metadata is not None else None,
        )

    @classmethod
    def rejected(
        cls,
        reason: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ConversionPipelineResult":
        return cls(
            status=ConversionPipelineStatus.REJECTED,
            rejection_reason=reason,
            message=message,
            metadata=dict(metadata) if metadata is not None else None,
        )

    @classmethod
    def failed(
        cls,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ConversionPipelineResult":
        return cls(
            status=ConversionPipelineStatus.FAILED,
            message=message,
            metadata=dict(metadata) if metadata is not None else None,
        )
