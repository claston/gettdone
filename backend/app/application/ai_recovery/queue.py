from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from app.application.ai_recovery.schemas import ObjectKey, Sha256Hex, ShortIdentifier, StrictContractModel


class AIRecoveryQueueMessage(StrictContractModel):
    schema_version: Literal[1] = 1
    event_type: Literal["ai_recovery.diagnosis_requested"] = "ai_recovery.diagnosis_requested"
    idempotency_key: Sha256Hex
    bucket: ShortIdentifier
    ready_key: ObjectKey
    attempt: int = Field(default=1, ge=1, le=20)


class AIRecoveryQueuePublisher(Protocol):
    def publish(self, message: AIRecoveryQueueMessage) -> str: ...
