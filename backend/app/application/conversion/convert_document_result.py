from dataclasses import dataclass
from enum import Enum
from typing import Any


class ConvertDocumentStatus(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConvertDocumentResult:
    analysis_id: str
    status: ConvertDocumentStatus
    payload: dict[str, Any] | None = None
    rejection_reason: str | None = None
    message: str | None = None

    @classmethod
    def completed(
        cls,
        analysis_id: str,
        payload: dict[str, Any],
    ) -> "ConvertDocumentResult":
        return cls(
            analysis_id=analysis_id,
            status=ConvertDocumentStatus.COMPLETED,
            payload=payload,
        )

    @classmethod
    def rejected(
        cls,
        analysis_id: str,
        reason: str,
        message: str | None = None,
    ) -> "ConvertDocumentResult":
        return cls(
            analysis_id=analysis_id,
            status=ConvertDocumentStatus.REJECTED,
            rejection_reason=reason,
            message=message,
        )

    @classmethod
    def failed(
        cls,
        analysis_id: str,
        message: str | None = None,
    ) -> "ConvertDocumentResult":
        return cls(
            analysis_id=analysis_id,
            status=ConvertDocumentStatus.FAILED,
            message=message,
        )
