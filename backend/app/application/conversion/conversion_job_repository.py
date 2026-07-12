from __future__ import annotations

import hashlib
import json
import re
from dataclasses import MISSING, asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.document_preflight_service import DocumentPreflightResult

_JOB_ID_PATTERN = re.compile(r"^job_[A-Za-z0-9_-]{1,64}$")


class ConversionJobStatus(str, Enum):
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConversionJobResultReference:
    analysis_id: str


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


class FilesystemConversionJobRepository:
    """Local durable job registry behind a worker-replaceable repository contract."""

    def __init__(
        self,
        *,
        root_dir: Path,
        active_ttl_seconds: int = 24 * 60 * 60,
        terminal_ttl_seconds: int = 24 * 60 * 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root_dir = root_dir.resolve()
        self.records_dir = self.root_dir / "records"
        self.idempotency_dir = self.root_dir / "idempotency"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.idempotency_dir.mkdir(parents=True, exist_ok=True)
        self.active_ttl_seconds = max(1, int(active_ttl_seconds))
        self.terminal_ttl_seconds = max(1, int(terminal_ttl_seconds))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def submit(self, job: ConversionJob) -> ConversionJobSubmission:
        self._validate_job(job)
        with self._lock:
            index_path = self._idempotency_path(job.idempotency_key)
            if index_path.exists():
                existing_job_id = index_path.read_text(encoding="utf-8").strip()
                existing = self.get(existing_job_id)
                if existing is not None:
                    return ConversionJobSubmission(record=existing, created=False)
                index_path.unlink(missing_ok=True)

            if self.get(job.job_id) is not None:
                raise ValueError(f"Conversion job already exists: {job.job_id}")
            now = self._now()
            record = ConversionJobRecord(
                job=job,
                status=ConversionJobStatus.SUBMITTED,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self.active_ttl_seconds),
            )
            self._write_record(record)
            try:
                self._write_text_atomic(index_path, job.job_id)
            except Exception:
                self._record_path(job.job_id).unlink(missing_ok=True)
                raise
            return ConversionJobSubmission(record=record, created=True)

    def get(self, job_id: str) -> ConversionJobRecord | None:
        path = self._record_path(job_id)
        if not path.exists():
            return None
        return self._deserialize_record(json.loads(path.read_text(encoding="utf-8")))

    def mark_running(self, job_id: str) -> ConversionJobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status == ConversionJobStatus.RUNNING:
                return record
            self._require_transition(record, ConversionJobStatus.RUNNING, {ConversionJobStatus.SUBMITTED})
            return self._replace_status(record, status=ConversionJobStatus.RUNNING)

    def mark_completed(
        self,
        job_id: str,
        *,
        result: ConversionJobResultReference,
    ) -> ConversionJobRecord:
        analysis_id = result.analysis_id.strip()
        if not analysis_id:
            raise ValueError("Completed conversion job requires an analysis id.")
        normalized_result = ConversionJobResultReference(analysis_id=analysis_id)
        with self._lock:
            record = self._require(job_id)
            if record.status == ConversionJobStatus.COMPLETED and record.result == normalized_result:
                return record
            self._require_transition(record, ConversionJobStatus.COMPLETED, {ConversionJobStatus.RUNNING})
            return self._replace_status(
                record,
                status=ConversionJobStatus.COMPLETED,
                result=normalized_result,
                terminal=True,
            )

    def mark_failed(self, job_id: str, *, code: str, message: str | None = None) -> ConversionJobRecord:
        failure = ConversionJobFailure(
            code=(code or "conversion_failed").strip()[:120] or "conversion_failed",
            message=(message or "").strip()[:500] or None,
        )
        with self._lock:
            record = self._require(job_id)
            if record.status == ConversionJobStatus.FAILED and record.failure == failure:
                return record
            self._require_transition(
                record,
                ConversionJobStatus.FAILED,
                {ConversionJobStatus.SUBMITTED, ConversionJobStatus.RUNNING},
            )
            return self._replace_status(
                record,
                status=ConversionJobStatus.FAILED,
                failure=failure,
                terminal=True,
            )

    def list_expired(self, *, now: datetime | None = None) -> list[ConversionJobRecord]:
        cutoff = self._normalize_datetime(now) if now is not None else self._now()
        with self._lock:
            records = []
            for path in self.records_dir.glob("job_*.json"):
                record = self._deserialize_record(json.loads(path.read_text(encoding="utf-8")))
                if record.expires_at <= cutoff:
                    records.append(record)
            return sorted(records, key=lambda item: item.created_at)

    def delete(self, job_id: str) -> None:
        with self._lock:
            record = self.get(job_id)
            if record is None:
                return
            self._record_path(job_id).unlink(missing_ok=True)
            index_path = self._idempotency_path(record.job.idempotency_key)
            if index_path.exists() and index_path.read_text(encoding="utf-8").strip() == job_id:
                index_path.unlink(missing_ok=True)

    def _replace_status(
        self,
        record: ConversionJobRecord,
        *,
        status: ConversionJobStatus,
        result: ConversionJobResultReference | None = None,
        failure: ConversionJobFailure | None = None,
        terminal: bool = False,
    ) -> ConversionJobRecord:
        now = self._now()
        ttl = self.terminal_ttl_seconds if terminal else self.active_ttl_seconds
        updated = replace(
            record,
            status=status,
            updated_at=now,
            expires_at=now + timedelta(seconds=ttl),
            result=result,
            failure=failure,
        )
        self._write_record(updated)
        return updated

    def _require(self, job_id: str) -> ConversionJobRecord:
        record = self.get(job_id)
        if record is None:
            raise KeyError(f"Conversion job not found: {job_id}")
        return record

    @staticmethod
    def _require_transition(
        record: ConversionJobRecord,
        target: ConversionJobStatus,
        allowed: set[ConversionJobStatus],
    ) -> None:
        if record.status not in allowed:
            raise ValueError(f"Cannot transition conversion job from {record.status.value} to {target.value}.")

    def _write_record(self, record: ConversionJobRecord) -> None:
        payload = {
            "job": {
                "job_id": record.job.job_id,
                "idempotency_key": record.job.idempotency_key,
                "document": asdict(record.job.document),
                "identity": self._serialize_identity(record.job.identity),
                "preflight_result": asdict(record.job.preflight_result),
            },
            "status": record.status.value,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "result": asdict(record.result) if record.result is not None else None,
            "failure": asdict(record.failure) if record.failure is not None else None,
        }
        self._write_text_atomic(
            self._record_path(record.job.job_id),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def _deserialize_record(self, payload: dict) -> ConversionJobRecord:
        job_payload = payload["job"]
        job = ConversionJob(
            job_id=job_payload["job_id"],
            idempotency_key=job_payload["idempotency_key"],
            document=ConversionDocumentReference(**job_payload["document"]),
            identity=IdentityContext(**job_payload["identity"]),
            preflight_result=DocumentPreflightResult(**job_payload["preflight_result"]),
        )
        return ConversionJobRecord(
            job=job,
            status=ConversionJobStatus(payload["status"]),
            created_at=self._parse_datetime(payload["created_at"]),
            updated_at=self._parse_datetime(payload["updated_at"]),
            expires_at=self._parse_datetime(payload["expires_at"]),
            result=ConversionJobResultReference(**payload["result"]) if payload.get("result") else None,
            failure=ConversionJobFailure(**payload["failure"]) if payload.get("failure") else None,
        )

    @staticmethod
    def _serialize_identity(identity) -> dict:
        payload = {}
        for identity_field in fields(IdentityContext):
            if hasattr(identity, identity_field.name):
                payload[identity_field.name] = getattr(identity, identity_field.name)
            elif identity_field.default is not MISSING:
                payload[identity_field.name] = identity_field.default
            else:
                raise ValueError(f"Conversion identity is missing required field: {identity_field.name}")
        return payload

    def _record_path(self, job_id: str) -> Path:
        if _JOB_ID_PATTERN.fullmatch(job_id or "") is None:
            raise ValueError("Invalid conversion job id.")
        return self.records_dir / f"{job_id}.json"

    def _idempotency_path(self, key: str) -> Path:
        normalized = (key or "").strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("Invalid conversion job idempotency key.")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self.idempotency_dir / f"{digest}.txt"

    def _validate_job(self, job: ConversionJob) -> None:
        self._record_path(job.job_id)
        self._idempotency_path(job.idempotency_key)

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _now(self) -> datetime:
        return self._normalize_datetime(self.clock())

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Conversion job timestamps must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @classmethod
    def _parse_datetime(cls, value: str) -> datetime:
        return cls._normalize_datetime(datetime.fromisoformat(value))
