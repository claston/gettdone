from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import FilesystemConversionDocumentStore
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_job_cleanup_service import ConversionJobCleanupService
from app.application.conversion.conversion_job_repository import (
    ConversionJobResultReference,
    ConversionJobStatus,
    FilesystemConversionJobRepository,
)
from app.application.conversion.uploaded_document import ingest_uploaded_document


def _job(document_store: FilesystemConversionDocumentStore, *, idempotency_key: str) -> ConversionJob:
    document = ingest_uploaded_document("statement.csv", b"date,description,amount\n")
    return ConversionJob.create(
        document=document_store.store(document),
        identity=IdentityContext(identity_type="anonymous", identity_id="anon_123", quota_limit=3),
        idempotency_key=idempotency_key,
    )


def test_filesystem_job_repository_persists_lifecycle_and_result_reference(tmp_path: Path) -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    document_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "documents")
    repository = FilesystemConversionJobRepository(
        root_dir=tmp_path / "records",
        clock=lambda: now,
        active_ttl_seconds=60,
        terminal_ttl_seconds=300,
    )
    job = _job(document_store, idempotency_key="request-123")

    submission = repository.submit(job)
    running = repository.mark_running(job.job_id)
    completed = repository.mark_completed(
        job.job_id,
        result=ConversionJobResultReference(analysis_id="an_123"),
    )

    assert submission.created is True
    assert submission.record.status == ConversionJobStatus.SUBMITTED
    assert running.status == ConversionJobStatus.RUNNING
    assert completed.status == ConversionJobStatus.COMPLETED
    assert completed.result == ConversionJobResultReference(analysis_id="an_123")
    assert completed.expires_at == now + timedelta(seconds=300)

    reloaded_repository = FilesystemConversionJobRepository(root_dir=tmp_path / "records")
    assert reloaded_repository.get(job.job_id) == completed


def test_job_repository_returns_existing_submission_for_same_idempotency_key(tmp_path: Path) -> None:
    document_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "documents")
    repository = FilesystemConversionJobRepository(root_dir=tmp_path / "records")
    original = _job(document_store, idempotency_key="same-client-request")
    duplicate = _job(document_store, idempotency_key="same-client-request")

    first = repository.submit(original)
    second = repository.submit(duplicate)

    assert first.created is True
    assert second.created is False
    assert second.record.job == original
    assert repository.get(duplicate.job_id) is None


def test_job_repository_rejects_invalid_state_transition(tmp_path: Path) -> None:
    document_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "documents")
    repository = FilesystemConversionJobRepository(root_dir=tmp_path / "records")
    job = _job(document_store, idempotency_key="request-123")
    repository.submit(job)
    repository.mark_running(job.job_id)
    repository.mark_completed(job.job_id, result=ConversionJobResultReference(analysis_id="an_123"))

    with pytest.raises(ValueError, match="completed.*running"):
        repository.mark_running(job.job_id)


def test_cleanup_removes_expired_record_and_orphaned_document(tmp_path: Path) -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    clock_value = [now]
    document_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "documents")
    repository = FilesystemConversionJobRepository(
        root_dir=tmp_path / "records",
        clock=lambda: clock_value[0],
        active_ttl_seconds=60,
    )
    job = _job(document_store, idempotency_key="orphaned-request")
    repository.submit(job)
    clock_value[0] = now + timedelta(seconds=61)

    removed = ConversionJobCleanupService(
        job_repository=repository,
        document_store=document_store,
    ).cleanup_expired()

    assert removed == 1
    assert repository.get(job.job_id) is None
    with pytest.raises(FileNotFoundError):
        document_store.load(job.document)
