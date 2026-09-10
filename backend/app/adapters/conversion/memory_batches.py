from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.application.conversion.contracts.batches import (
    ConversionBatchSnapshot,
    ConversionBatchSubmission,
    ConversionOutboxEvent,
)
from app.application.conversion.contracts.jobs import (
    ConversionJobFailure,
    ConversionJobRecord,
    ConversionJobResultReference,
    ConversionJobStatus,
)
from app.application.conversion.conversion_batch import ConversionBatch, resolve_conversion_batch_status
from app.application.conversion.conversion_job import ConversionJob


class InMemoryConversionBatchRepository:
    """Thread-safe repository used for tests and local async-flow development."""

    def __init__(self, *, ttl_seconds: int = 24 * 60 * 60) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._batches: dict[str, ConversionBatch] = {}
        self._jobs: dict[str, ConversionJobRecord] = {}
        self._batch_jobs: dict[str, list[str]] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._outbox: dict[str, ConversionOutboxEvent] = {}
        self._outbox_job: dict[str, str] = {}
        self._lock = RLock()

    def create(self, *, batch: ConversionBatch, jobs: list[ConversionJob]) -> ConversionBatchSubmission:
        if len(jobs) != batch.files_count:
            raise ValueError("Conversion batch jobs count must match files_count.")
        if any(job.batch_id != batch.batch_id for job in jobs):
            raise ValueError("All conversion jobs must belong to the same batch.")
        if any(job.identity.identity_type != batch.identity_type or job.identity.identity_id != batch.identity_id for job in jobs):
            raise ValueError("All conversion jobs must have the same owner as their batch.")

        idempotency = (batch.identity_type, batch.identity_id, batch.idempotency_key)
        with self._lock:
            existing_batch_id = self._idempotency.get(idempotency)
            if existing_batch_id is not None:
                return ConversionBatchSubmission(snapshot=self._snapshot(existing_batch_id), created=False)
            if batch.batch_id in self._batches or any(job.job_id in self._jobs for job in jobs):
                raise ValueError("Conversion batch or job already exists.")

            expires_at = batch.created_at + timedelta(seconds=self.ttl_seconds)
            self._batches[batch.batch_id] = batch
            self._batch_jobs[batch.batch_id] = []
            for job in jobs:
                self._jobs[job.job_id] = ConversionJobRecord(
                    job=job,
                    status=ConversionJobStatus.UPLOADING,
                    created_at=batch.created_at,
                    updated_at=batch.created_at,
                    expires_at=expires_at,
                )
                self._batch_jobs[batch.batch_id].append(job.job_id)
            self._idempotency[idempotency] = batch.batch_id
            return ConversionBatchSubmission(snapshot=self._snapshot(batch.batch_id), created=True)

    def get(self, job_id: str) -> ConversionJobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_for_owner(
        self,
        batch_id: str,
        *,
        identity_type: str,
        identity_id: str,
    ) -> ConversionBatchSnapshot | None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or (batch.identity_type, batch.identity_id) != (identity_type, identity_id):
                return None
            return self._snapshot(batch_id)

    def mark_uploaded(self, job_id: str) -> ConversionJobRecord:
        return self._transition(job_id, ConversionJobStatus.UPLOADED, {ConversionJobStatus.UPLOADING})

    def mark_queued(self, job_id: str) -> ConversionJobRecord:
        return self._transition(
            job_id,
            ConversionJobStatus.QUEUED,
            {ConversionJobStatus.UPLOADED, ConversionJobStatus.SUBMITTED, ConversionJobStatus.RETRYING},
        )

    def mark_running(self, job_id: str) -> ConversionJobRecord:
        return self._transition(
            job_id,
            ConversionJobStatus.RUNNING,
            {ConversionJobStatus.SUBMITTED, ConversionJobStatus.QUEUED},
        )

    def claim_for_processing(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ConversionJobRecord | None:
        _ = (lease_owner, lease_seconds)
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            if current.status == ConversionJobStatus.RUNNING:
                return None
        return self._transition(
            job_id,
            ConversionJobStatus.RUNNING,
            {
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.QUEUED,
                ConversionJobStatus.RETRYING,
            },
        )

    def mark_retrying(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord:
        return self._transition(
            job_id,
            ConversionJobStatus.RETRYING,
            {ConversionJobStatus.RUNNING},
            failure=self._failure(code, message),
        )

    def mark_completed(
        self,
        job_id: str,
        *,
        result: ConversionJobResultReference,
    ) -> ConversionJobRecord:
        if not (result.analysis_id or "").strip():
            raise ValueError("Completed conversion job requires an analysis id.")
        return self._transition(
            job_id,
            ConversionJobStatus.COMPLETED,
            {ConversionJobStatus.RUNNING},
            result=result,
        )

    def mark_failed(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord:
        return self._transition(
            job_id,
            ConversionJobStatus.FAILED,
            {
                ConversionJobStatus.UPLOADING,
                ConversionJobStatus.UPLOADED,
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.QUEUED,
                ConversionJobStatus.RUNNING,
                ConversionJobStatus.RETRYING,
            },
            failure=self._failure(code, message),
        )

    def stage_for_queue(self, job_id: str, *, trace_id: str) -> ConversionOutboxEvent | None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            existing_event_id = self._outbox_job.get(job_id)
            if existing_event_id is not None:
                return self._outbox[existing_event_id]
            if current.status == ConversionJobStatus.QUEUED:
                return None
            if current.status not in {
                ConversionJobStatus.UPLOADED,
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.RETRYING,
            }:
                raise ValueError(f"Cannot stage conversion job from {current.status.value} for queue.")
            self._transition(job_id, ConversionJobStatus.QUEUED, {current.status})
            event = ConversionOutboxEvent(
                event_id=f"evt_{uuid4().hex[:24]}",
                job_id=job_id,
                batch_id=current.job.batch_id or "",
                trace_id=trace_id,
                created_at=datetime.now(timezone.utc),
            )
            self._outbox[event.event_id] = event
            self._outbox_job[job_id] = event.event_id
            return event

    def list_pending_outbox(self, *, limit: int = 100) -> list[ConversionOutboxEvent]:
        with self._lock:
            return sorted(self._outbox.values(), key=lambda item: item.created_at)[: max(1, min(limit, 1000))]

    def mark_outbox_published(self, event_id: str) -> None:
        with self._lock:
            event = self._outbox.pop(event_id, None)
            if event is not None:
                self._outbox_job.pop(event.job_id, None)

    def record_outbox_failure(self, event_id: str, *, message: str) -> None:
        _ = (event_id, message)

    def _transition(
        self,
        job_id: str,
        target: ConversionJobStatus,
        allowed: set[ConversionJobStatus],
        *,
        result: ConversionJobResultReference | None = None,
        failure: ConversionJobFailure | None = None,
    ) -> ConversionJobRecord:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            if current.status == target:
                return current
            if current.status not in allowed:
                raise ValueError(f"Cannot transition conversion job from {current.status.value} to {target.value}.")
            updated = replace(
                current,
                status=target,
                updated_at=datetime.now(timezone.utc),
                result=result,
                failure=failure,
            )
            self._jobs[job_id] = updated
            return updated

    def _snapshot(self, batch_id: str) -> ConversionBatchSnapshot:
        jobs = tuple(self._jobs[job_id] for job_id in self._batch_jobs[batch_id])
        batch = replace(
            self._batches[batch_id],
            status=resolve_conversion_batch_status([job.status for job in jobs]),
        )
        return ConversionBatchSnapshot(batch=batch, jobs=jobs)

    @staticmethod
    def _failure(code: str, message: str | None) -> ConversionJobFailure:
        return ConversionJobFailure(
            code=(code or "conversion_failed").strip()[:120] or "conversion_failed",
            message=(message or "").strip()[:500] or None,
        )
