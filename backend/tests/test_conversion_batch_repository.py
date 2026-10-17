from datetime import datetime, timezone

import pytest

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_batch import ConversionBatch, ConversionBatchStatus
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_job_repository import ConversionJobResultReference, ConversionJobStatus


def _job(number: int) -> ConversionJob:
    return ConversionJob.create(
        job_id=f"job_{number}",
        idempotency_key=f"batch-request:{number}",
        batch_id="batch_test",
        document=ConversionDocumentReference(
            storage_key=f"doc_{number:024x}",
            filename=f"statement-{number}.pdf",
            file_type="pdf",
            size_bytes=100 + number,
            sha256_hex=f"{number:064x}",
        ),
        identity=IdentityContext(identity_type="user", identity_id="usr_123", quota_limit=20),
    )


def _batch() -> ConversionBatch:
    return ConversionBatch(
        batch_id="batch_test",
        identity_type="user",
        identity_id="usr_123",
        files_count=2,
        idempotency_key="batch-request",
        status=ConversionBatchStatus.UPLOADING,
        created_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_batch_repository_is_idempotent_and_owner_scoped() -> None:
    repository = InMemoryConversionBatchRepository()
    original = repository.create(batch=_batch(), jobs=[_job(1), _job(2)])
    duplicate = repository.create(batch=_batch(), jobs=[_job(1), _job(2)])

    assert original.created is True
    assert duplicate.created is False
    assert duplicate.snapshot == original.snapshot
    assert [item.status for item in original.snapshot.jobs] == [
        ConversionJobStatus.UPLOADING,
        ConversionJobStatus.UPLOADING,
    ]
    assert repository.get_for_owner("batch_test", identity_type="user", identity_id="usr_123") is not None
    assert repository.get_for_owner("batch_test", identity_type="user", identity_id="usr_other") is None


def test_batch_repository_supports_independent_job_completion_and_retry() -> None:
    repository = InMemoryConversionBatchRepository()
    repository.create(batch=_batch(), jobs=[_job(1), _job(2)])

    repository.mark_uploaded("job_1")
    repository.mark_queued("job_1")
    repository.mark_running("job_1")
    repository.mark_completed("job_1", result=ConversionJobResultReference(analysis_id="an_1"))
    repository.mark_uploaded("job_2")
    repository.mark_queued("job_2")
    repository.mark_running("job_2")
    repository.mark_retrying("job_2", code="TimeoutError", message="retry later")
    repository.mark_queued("job_2")
    repository.mark_running("job_2")
    repository.mark_failed("job_2", code="InvalidPdf", message="invalid")

    snapshot = repository.get_for_owner("batch_test", identity_type="user", identity_id="usr_123")

    assert snapshot is not None
    assert snapshot.batch.status == ConversionBatchStatus.COMPLETED_WITH_ERRORS
    assert snapshot.jobs[0].result == ConversionJobResultReference(analysis_id="an_1")
    assert snapshot.jobs[1].failure is not None
    assert snapshot.jobs[1].failure.code == "InvalidPdf"


def test_batch_repository_rejects_job_from_another_batch() -> None:
    repository = InMemoryConversionBatchRepository()
    mismatched = _job(1)
    object.__setattr__(mismatched, "batch_id", "batch_other")

    with pytest.raises(ValueError, match="same batch"):
        repository.create(batch=_batch(), jobs=[mismatched, _job(2)])


def test_batch_repository_rejects_invalid_transition_without_corrupting_state() -> None:
    repository = InMemoryConversionBatchRepository()
    repository.create(batch=_batch(), jobs=[_job(1), _job(2)])

    with pytest.raises(ValueError, match="uploading.*running"):
        repository.mark_running("job_1")

    assert repository.get("job_1").status == ConversionJobStatus.UPLOADING


def test_batch_snapshot_keeps_saved_rows_and_never_regresses_to_an_older_save() -> None:
    repository = InMemoryConversionBatchRepository()
    repository.create(batch=_batch(), jobs=[_job(1), _job(2)])
    repository.mark_uploaded("job_1")
    repository.mark_queued("job_1")
    repository.mark_running("job_1")
    repository.mark_completed("job_1", result=ConversionJobResultReference(
        analysis_id="an_1", payload={"quota_remaining": 18, "analysis": {
            "bank_name": "Itaú", "updated_at": "2026-10-01T00:00:00Z",
        }},
    ))
    repository.mark_reviewed("job_1", reviewed_at="2026-10-01")
    saved = {"updated_at": "2026-10-02T00:00:00Z", "preview_transactions": [
        {"description": "Corrigido", "source_page": 3},
    ]}
    repository.update_analysis("job_1", analysis=saved)
    repository.update_analysis("job_1", analysis={"updated_at": "2026-10-01T00:00:00Z", "preview_transactions": []})
    payload = repository.get("job_1").result.payload
    assert payload["analysis"] == {"bank_name": "Itaú", **saved}
    assert payload["quota_remaining"] == 18
    assert "reviewed_at" not in payload
