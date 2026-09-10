from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from app.application.conversion.contracts.jobs import ConversionJobStatus

MAX_CONVERSION_BATCH_FILES = 12


class ConversionBatchStatus(str, Enum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConversionBatch:
    batch_id: str
    identity_type: str
    identity_id: str
    files_count: int
    idempotency_key: str
    status: ConversionBatchStatus
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        identity_type: str,
        identity_id: str,
        files_count: int,
        idempotency_key: str,
        created_at: datetime | None = None,
    ) -> ConversionBatch:
        normalized_identity_type = (identity_type or "").strip()
        normalized_identity_id = (identity_id or "").strip()
        normalized_idempotency_key = (idempotency_key or "").strip()
        if not normalized_identity_type or not normalized_identity_id:
            raise ValueError("Conversion batch requires an owner identity.")
        if not normalized_idempotency_key:
            raise ValueError("Conversion batch requires an idempotency key.")
        if not 1 <= files_count <= MAX_CONVERSION_BATCH_FILES:
            raise ValueError("Conversion batch files_count must be between 1 and 12.")

        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("Conversion batch timestamps must be timezone-aware.")

        return cls(
            batch_id=f"batch_{uuid4().hex}",
            identity_type=normalized_identity_type,
            identity_id=normalized_identity_id,
            files_count=files_count,
            idempotency_key=normalized_idempotency_key,
            status=ConversionBatchStatus.UPLOADING,
            created_at=timestamp.astimezone(timezone.utc),
        )


def resolve_conversion_batch_status(statuses: list[ConversionJobStatus]) -> ConversionBatchStatus:
    if not statuses:
        raise ValueError("Conversion batch requires at least one job status.")

    unique = set(statuses)
    terminal = {ConversionJobStatus.COMPLETED, ConversionJobStatus.FAILED, ConversionJobStatus.EXPIRED}
    if unique <= terminal:
        if unique == {ConversionJobStatus.COMPLETED}:
            return ConversionBatchStatus.COMPLETED
        if ConversionJobStatus.COMPLETED in unique:
            return ConversionBatchStatus.COMPLETED_WITH_ERRORS
        return ConversionBatchStatus.FAILED

    if unique <= {ConversionJobStatus.UPLOADING, ConversionJobStatus.UPLOADED}:
        return ConversionBatchStatus.UPLOADING
    if unique <= {ConversionJobStatus.SUBMITTED, ConversionJobStatus.QUEUED}:
        return ConversionBatchStatus.QUEUED
    return ConversionBatchStatus.PROCESSING
