from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.application.conversion.contracts.jobs import (
    ConversionJobRecord,
    ConversionJobResultReference,
)
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob


@dataclass(frozen=True, slots=True)
class ConversionBatchSnapshot:
    batch: ConversionBatch
    jobs: tuple[ConversionJobRecord, ...]


@dataclass(frozen=True, slots=True)
class ConversionBatchSubmission:
    snapshot: ConversionBatchSnapshot
    created: bool


@dataclass(frozen=True, slots=True)
class ConversionOutboxEvent:
    event_id: str
    job_id: str
    batch_id: str
    trace_id: str
    created_at: datetime


class ConversionBatchRepository(Protocol):
    def create(self, *, batch: ConversionBatch, jobs: list[ConversionJob]) -> ConversionBatchSubmission: ...

    def get(self, job_id: str) -> ConversionJobRecord | None: ...

    def get_for_owner(
        self,
        batch_id: str,
        *,
        identity_type: str,
        identity_id: str,
    ) -> ConversionBatchSnapshot | None: ...

    def mark_uploaded(self, job_id: str) -> ConversionJobRecord: ...

    def mark_queued(self, job_id: str) -> ConversionJobRecord: ...

    def mark_running(self, job_id: str) -> ConversionJobRecord: ...

    def claim_for_processing(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ConversionJobRecord | None: ...

    def mark_retrying(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord: ...

    def mark_completed(
        self,
        job_id: str,
        *,
        result: ConversionJobResultReference,
    ) -> ConversionJobRecord: ...

    def mark_failed(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord: ...

    def stage_for_queue(self, job_id: str, *, trace_id: str) -> ConversionOutboxEvent | None: ...

    def list_pending_outbox(self, *, limit: int = 100) -> list[ConversionOutboxEvent]: ...

    def mark_outbox_published(self, event_id: str) -> None: ...

    def record_outbox_failure(self, event_id: str, *, message: str) -> None: ...
