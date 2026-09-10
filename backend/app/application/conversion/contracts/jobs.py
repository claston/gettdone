from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.application.conversion.conversion_job import ConversionJob


class ConversionJobStatus(str, Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    SUBMITTED = "submitted"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ConversionJobResultReference:
    analysis_id: str
    payload: dict[str, object] | None = None
    s3_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ConversionJobFailure:
    code: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ConversionJobRecord:
    job: ConversionJob
    status: ConversionJobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    result: ConversionJobResultReference | None = None
    failure: ConversionJobFailure | None = None


@dataclass(frozen=True, slots=True)
class ConversionJobSubmission:
    record: ConversionJobRecord
    created: bool


class ConversionJobRepository(Protocol):
    def submit(self, job: ConversionJob) -> ConversionJobSubmission: ...

    def get(self, job_id: str) -> ConversionJobRecord | None: ...

    def mark_running(self, job_id: str) -> ConversionJobRecord: ...

    def mark_completed(
        self,
        job_id: str,
        *,
        result: ConversionJobResultReference,
    ) -> ConversionJobRecord: ...

    def mark_failed(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord: ...

    def list_expired(self, *, now: datetime | None = None) -> list[ConversionJobRecord]: ...

    def delete(self, job_id: str) -> None: ...
