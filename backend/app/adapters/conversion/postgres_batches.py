from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from app.application.conversion.contracts.batches import ConversionBatchSnapshot, ConversionBatchSubmission, ConversionOutboxEvent
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import (
    ConversionJobFailure,
    ConversionJobRecord,
    ConversionJobResultReference,
    ConversionJobStatus,
    ConversionJobSubmission,
)
from app.application.conversion.contracts.preflight import DocumentPreflightResult
from app.application.conversion.conversion_batch import ConversionBatch, ConversionBatchStatus, resolve_conversion_batch_status
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.identity import IdentityContext

_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ConnectionFactory = Callable[[], Any]


class PostgresConversionBatchRepository:
    """Shared conversion state for the Render API and Lambda workers."""

    def __init__(
        self,
        *,
        database_url: str,
        database_schema: str = "public",
        active_ttl_seconds: int = 24 * 60 * 60,
        terminal_ttl_seconds: int = 24 * 60 * 60,
        connection_factory: ConnectionFactory | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_url = (database_url or "").strip()
        if not self.database_url and connection_factory is None:
            raise ValueError("PostgreSQL conversion repository requires DATABASE_URL.")
        self.database_schema = (database_schema or "public").strip() or "public"
        if _SCHEMA_PATTERN.fullmatch(self.database_schema) is None:
            raise ValueError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
        self.active_ttl_seconds = max(1, int(active_ttl_seconds))
        self.terminal_ttl_seconds = max(1, int(terminal_ttl_seconds))
        self.connection_factory = connection_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._batches_table = f'"{self.database_schema}".conversion_batches'
        self._jobs_table = f'"{self.database_schema}".conversion_jobs'
        self._outbox_table = f'"{self.database_schema}".conversion_outbox'

    def submit(self, job: ConversionJob) -> ConversionJobSubmission:
        batch_id = job.batch_id or f"batch_{job.job_id.removeprefix('job_')}"
        normalized_job = replace(job, batch_id=batch_id)
        created_at = self._normalize_datetime(self.clock())
        batch = ConversionBatch(
            batch_id=batch_id,
            identity_type=job.identity.identity_type,
            identity_id=job.identity.identity_id,
            files_count=1,
            idempotency_key=f"legacy:{job.idempotency_key}",
            status=ConversionBatchStatus.UPLOADING,
            created_at=created_at,
        )
        submission = self.create(batch=batch, jobs=[normalized_job])
        record = submission.snapshot.jobs[0]
        if record.status == ConversionJobStatus.UPLOADING:
            record = self._transition(
                record.job.job_id,
                ConversionJobStatus.SUBMITTED,
                {ConversionJobStatus.UPLOADING},
            )
        return ConversionJobSubmission(record=record, created=submission.created)

    def create(self, *, batch: ConversionBatch, jobs: list[ConversionJob]) -> ConversionBatchSubmission:
        if len(jobs) != batch.files_count:
            raise ValueError("Conversion batch jobs count must match files_count.")
        if any(job.batch_id != batch.batch_id for job in jobs):
            raise ValueError("All conversion jobs must belong to the same batch.")
        if any(job.identity.identity_type != batch.identity_type or job.identity.identity_id != batch.identity_id for job in jobs):
            raise ValueError("All conversion jobs must have the same owner as their batch.")

        with self._connection() as conn:
            existing = self._find_batch_by_idempotency(
                conn,
                identity_type=batch.identity_type,
                identity_id=batch.identity_id,
                idempotency_key=batch.idempotency_key,
            )
            if existing is not None:
                return ConversionBatchSubmission(snapshot=self._snapshot(conn, existing["batch_id"]), created=False)

            expires_at = batch.created_at + timedelta(seconds=self.active_ttl_seconds)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._batches_table} (
                        batch_id, identity_type, identity_id, idempotency_key, files_count,
                        status, created_at, updated_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (identity_type, identity_id, idempotency_key) DO NOTHING
                    RETURNING batch_id
                    """,
                    (
                        batch.batch_id,
                        batch.identity_type,
                        batch.identity_id,
                        batch.idempotency_key,
                        batch.files_count,
                        ConversionBatchStatus.UPLOADING.value,
                        batch.created_at,
                        batch.created_at,
                        expires_at,
                    ),
                )
                if cursor.fetchone() is None:
                    existing = self._find_batch_by_idempotency(
                        conn,
                        identity_type=batch.identity_type,
                        identity_id=batch.identity_id,
                        idempotency_key=batch.idempotency_key,
                    )
                    if existing is None:  # pragma: no cover - defensive database consistency guard
                        raise RuntimeError("Concurrent conversion batch creation could not be resolved.")
                    return ConversionBatchSubmission(
                        snapshot=self._snapshot(conn, existing["batch_id"]),
                        created=False,
                    )
                for job in jobs:
                    cursor.execute(
                        f"""
                        INSERT INTO {self._jobs_table} (
                            job_id, batch_id, idempotency_key, status, document, identity,
                            preflight_result, created_at, updated_at, expires_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            job.job_id,
                            batch.batch_id,
                            job.idempotency_key,
                            ConversionJobStatus.UPLOADING.value,
                            self._jsonb(asdict(job.document)),
                            self._jsonb(asdict(job.identity)),
                            self._jsonb(asdict(job.preflight_result)),
                            batch.created_at,
                            batch.created_at,
                            expires_at,
                        ),
                    )
            return ConversionBatchSubmission(snapshot=self._snapshot(conn, batch.batch_id), created=True)

    def get(self, job_id: str) -> ConversionJobRecord | None:
        with self._connection() as conn:
            row = self._fetch_job(conn, job_id)
            return self._record_from_row(row) if row is not None else None

    def get_for_owner(
        self,
        batch_id: str,
        *,
        identity_type: str,
        identity_id: str,
    ) -> ConversionBatchSnapshot | None:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {self._batches_table}
                    WHERE batch_id = %s AND identity_type = %s AND identity_id = %s
                    """,
                    (batch_id, identity_type, identity_id),
                )
                if cursor.fetchone() is None:
                    return None
            return self._snapshot(conn, batch_id)

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
            increment_attempt=True,
        )

    def claim_for_processing(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ConversionJobRecord | None:
        normalized_owner = (lease_owner or "").strip()[:200]
        if not normalized_owner:
            raise ValueError("Conversion processing lease requires an owner.")
        now = self._normalize_datetime(self.clock())
        lease_expires_at = now + timedelta(seconds=max(30, int(lease_seconds)))
        with self._connection() as conn:
            current_row = self._fetch_job(conn, job_id, for_update=True)
            if current_row is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            current = self._record_from_row(current_row)
            if current.status in {
                ConversionJobStatus.COMPLETED,
                ConversionJobStatus.FAILED,
                ConversionJobStatus.EXPIRED,
            }:
                return None
            current_lease_expires_at = current_row.get("lease_expires_at")
            if (
                current.status == ConversionJobStatus.RUNNING
                and current_lease_expires_at is not None
                and self._normalize_datetime(current_lease_expires_at) > now
            ):
                return None
            allowed = {
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.QUEUED,
                ConversionJobStatus.RETRYING,
                ConversionJobStatus.RUNNING,
            }
            if current.status not in allowed:
                raise ValueError(f"Cannot claim conversion job from {current.status.value}.")
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._jobs_table}
                    SET status = %s,
                        updated_at = %s,
                        expires_at = %s,
                        attempt_count = attempt_count + 1,
                        lease_owner = %s,
                        lease_expires_at = %s,
                        failure_code = NULL,
                        failure_message = NULL
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        ConversionJobStatus.RUNNING.value,
                        now,
                        now + timedelta(seconds=self.active_ttl_seconds),
                        normalized_owner,
                        lease_expires_at,
                        job_id,
                    ),
                )
                claimed = self._record_from_row(cursor.fetchone())
            self._refresh_batch_status(conn, claimed.job.batch_id)
            return claimed

    def mark_retrying(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord:
        failure = self._failure(code, message)
        return self._transition(
            job_id,
            ConversionJobStatus.RETRYING,
            {ConversionJobStatus.RUNNING},
            failure=failure,
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
            terminal=True,
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
            terminal=True,
        )

    def list_expired(self, *, now: datetime | None = None) -> list[ConversionJobRecord]:
        cutoff = self._normalize_datetime(now or self.clock())
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._jobs_table} WHERE expires_at <= %s ORDER BY created_at",
                    (cutoff,),
                )
                return [self._record_from_row(row) for row in cursor.fetchall()]

    def delete(self, job_id: str) -> None:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._jobs_table} WHERE job_id = %s", (job_id,))

    def stage_for_queue(self, job_id: str, *, trace_id: str) -> ConversionOutboxEvent | None:
        now = self._normalize_datetime(self.clock())
        with self._connection() as conn:
            current_row = self._fetch_job(conn, job_id, for_update=True)
            if current_row is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            current = self._record_from_row(current_row)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._outbox_table} WHERE job_id = %s AND published_at IS NULL",
                    (job_id,),
                )
                pending = cursor.fetchone()
            if pending is not None:
                return self._outbox_from_row(pending)
            if current.status == ConversionJobStatus.QUEUED:
                return None
            if current.status not in {
                ConversionJobStatus.UPLOADED,
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.RETRYING,
            }:
                raise ValueError(f"Cannot stage conversion job from {current.status.value} for queue.")

            event = ConversionOutboxEvent(
                event_id=f"evt_{uuid4().hex[:24]}",
                job_id=job_id,
                batch_id=current.job.batch_id or "",
                trace_id=trace_id,
                created_at=now,
            )
            payload = {
                "schema_version": 1,
                "event_type": "conversion.requested",
                "job_id": event.job_id,
                "batch_id": event.batch_id,
                "trace_id": event.trace_id,
            }
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._jobs_table}
                    SET status = %s, updated_at = %s, failure_code = NULL, failure_message = NULL,
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE job_id = %s
                    """,
                    (ConversionJobStatus.QUEUED.value, now, job_id),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {self._outbox_table} (
                        event_id, job_id, event_type, payload, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (event.event_id, job_id, "conversion.requested", self._jsonb(payload), now),
                )
            self._refresh_batch_status(conn, current.job.batch_id)
            return event

    def list_pending_outbox(self, *, limit: int = 100) -> list[ConversionOutboxEvent]:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {self._outbox_table}
                    WHERE published_at IS NULL
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (max(1, min(int(limit), 1000)),),
                )
                return [self._outbox_from_row(row) for row in cursor.fetchall()]

    def mark_outbox_published(self, event_id: str) -> None:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._outbox_table}
                    SET published_at = %s, publish_attempts = publish_attempts + 1, last_error = NULL
                    WHERE event_id = %s AND published_at IS NULL
                    """,
                    (self._normalize_datetime(self.clock()), event_id),
                )

    def record_outbox_failure(self, event_id: str, *, message: str) -> None:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._outbox_table}
                    SET publish_attempts = publish_attempts + 1, last_error = %s
                    WHERE event_id = %s AND published_at IS NULL
                    """,
                    ((message or "").strip()[:500] or None, event_id),
                )

    def _transition(
        self,
        job_id: str,
        target: ConversionJobStatus,
        allowed: set[ConversionJobStatus],
        *,
        result: ConversionJobResultReference | None = None,
        failure: ConversionJobFailure | None = None,
        increment_attempt: bool = False,
        terminal: bool = False,
    ) -> ConversionJobRecord:
        now = self._normalize_datetime(self.clock())
        ttl = self.terminal_ttl_seconds if terminal else self.active_ttl_seconds
        with self._connection() as conn:
            current_row = self._fetch_job(conn, job_id, for_update=True)
            if current_row is None:
                raise KeyError(f"Conversion job not found: {job_id}")
            current = self._record_from_row(current_row)
            if current.status == target:
                return current
            if current.status not in allowed:
                raise ValueError(f"Cannot transition conversion job from {current.status.value} to {target.value}.")

            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._jobs_table}
                    SET status = %s,
                        updated_at = %s,
                        expires_at = %s,
                        attempt_count = attempt_count + %s,
                        result_analysis_id = %s,
                        result_payload = %s,
                        result_s3_prefix = %s,
                        failure_code = %s,
                        failure_message = %s,
                        lease_owner = NULL,
                        lease_expires_at = NULL
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        target.value,
                        now,
                        now + timedelta(seconds=ttl),
                        1 if increment_attempt else 0,
                        result.analysis_id if result else None,
                        self._jsonb(result.payload) if result and result.payload is not None else None,
                        result.s3_prefix if result else None,
                        failure.code if failure else None,
                        failure.message if failure else None,
                        job_id,
                    ),
                )
                updated = self._record_from_row(cursor.fetchone())
            self._refresh_batch_status(conn, updated.job.batch_id)
            return updated

    def _snapshot(self, conn, batch_id: str) -> ConversionBatchSnapshot:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {self._batches_table} WHERE batch_id = %s", (batch_id,))
            batch_row = cursor.fetchone()
            if batch_row is None:
                raise KeyError(f"Conversion batch not found: {batch_id}")
            cursor.execute(
                f"SELECT * FROM {self._jobs_table} WHERE batch_id = %s ORDER BY created_at, job_id",
                (batch_id,),
            )
            jobs = tuple(self._record_from_row(row) for row in cursor.fetchall())

        batch = self._batch_from_row(batch_row)
        derived_status = resolve_conversion_batch_status([job.status for job in jobs])
        return ConversionBatchSnapshot(batch=replace(batch, status=derived_status), jobs=jobs)

    def _refresh_batch_status(self, conn, batch_id: str | None) -> None:
        if not batch_id:
            return
        snapshot = self._snapshot(conn, batch_id)
        now = self._normalize_datetime(self.clock())
        terminal = snapshot.batch.status in {
            ConversionBatchStatus.COMPLETED,
            ConversionBatchStatus.COMPLETED_WITH_ERRORS,
            ConversionBatchStatus.FAILED,
        }
        ttl = self.terminal_ttl_seconds if terminal else self.active_ttl_seconds
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self._batches_table}
                SET status = %s, updated_at = %s, expires_at = %s
                WHERE batch_id = %s
                """,
                (snapshot.batch.status.value, now, now + timedelta(seconds=ttl), batch_id),
            )

    def _find_batch_by_idempotency(
        self,
        conn,
        *,
        identity_type: str,
        identity_id: str,
        idempotency_key: str,
    ):
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM {self._batches_table}
                WHERE identity_type = %s AND identity_id = %s AND idempotency_key = %s
                """,
                (identity_type, identity_id, idempotency_key),
            )
            return cursor.fetchone()

    def _fetch_job(self, conn, job_id: str, *, for_update: bool = False):
        suffix = " FOR UPDATE" if for_update else ""
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {self._jobs_table} WHERE job_id = %s{suffix}", (job_id,))
            return cursor.fetchone()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self.connection_factory is not None:
            with self.connection_factory() as conn:
                yield conn
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - installed in production
            raise RuntimeError("PostgreSQL conversion repository requires psycopg.") from exc
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    @staticmethod
    def _jsonb(value: dict[str, object]):
        try:
            from psycopg.types.json import Jsonb
        except Exception as exc:  # pragma: no cover - installed in production
            raise RuntimeError("PostgreSQL conversion repository requires psycopg JSON support.") from exc
        return Jsonb(value)

    @classmethod
    def _record_from_row(cls, row) -> ConversionJobRecord:
        document_payload = cls._mapping_value(row["document"])
        identity_payload = cls._mapping_value(row["identity"])
        preflight_payload = cls._mapping_value(row["preflight_result"])
        job = ConversionJob(
            job_id=row["job_id"],
            idempotency_key=row["idempotency_key"],
            batch_id=row["batch_id"],
            document=ConversionDocumentReference(**document_payload),
            identity=IdentityContext(**identity_payload),
            preflight_result=DocumentPreflightResult(**preflight_payload),
        )
        result_analysis_id = str(row.get("result_analysis_id") or "").strip()
        result_payload = row.get("result_payload")
        failure_code = str(row.get("failure_code") or "").strip()
        return ConversionJobRecord(
            job=job,
            status=ConversionJobStatus(row["status"]),
            created_at=cls._normalize_datetime(row["created_at"]),
            updated_at=cls._normalize_datetime(row["updated_at"]),
            expires_at=cls._normalize_datetime(row["expires_at"]),
            result=(
                ConversionJobResultReference(
                    analysis_id=result_analysis_id,
                    payload=cls._mapping_value(result_payload) if result_payload is not None else None,
                    s3_prefix=str(row.get("result_s3_prefix") or "").strip() or None,
                )
                if result_analysis_id
                else None
            ),
            failure=(
                ConversionJobFailure(code=failure_code, message=str(row.get("failure_message") or "").strip() or None)
                if failure_code
                else None
            ),
        )

    @classmethod
    def _batch_from_row(cls, row) -> ConversionBatch:
        return ConversionBatch(
            batch_id=row["batch_id"],
            identity_type=row["identity_type"],
            identity_id=row["identity_id"],
            files_count=int(row["files_count"]),
            idempotency_key=row["idempotency_key"],
            status=ConversionBatchStatus(row["status"]),
            created_at=cls._normalize_datetime(row["created_at"]),
        )

    @classmethod
    def _outbox_from_row(cls, row) -> ConversionOutboxEvent:
        payload = cls._mapping_value(row["payload"])
        return ConversionOutboxEvent(
            event_id=row["event_id"],
            job_id=row["job_id"],
            batch_id=str(payload.get("batch_id") or ""),
            trace_id=str(payload.get("trace_id") or ""),
            created_at=cls._normalize_datetime(row["created_at"]),
        )

    @staticmethod
    def _mapping_value(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            import json

            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("Invalid JSON object stored for conversion job.")

    @staticmethod
    def _failure(code: str, message: str | None) -> ConversionJobFailure:
        return ConversionJobFailure(
            code=(code or "conversion_failed").strip()[:120] or "conversion_failed",
            message=(message or "").strip()[:500] or None,
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Conversion timestamps must be timezone-aware.")
        return value.astimezone(timezone.utc)
