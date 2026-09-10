from __future__ import annotations

from dataclasses import dataclass

from app.application.conversion.contracts.batches import ConversionBatchRepository, ConversionBatchSnapshot, ConversionOutboxEvent
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import ConversionJobStatus
from app.application.conversion.contracts.uploads import DirectUploadService, QueuePublisher, canonical_document_content_type
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class ConversionBatchFile:
    filename: str
    content_type: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class PreparedConversionBatchUpload:
    job_id: str
    document: ConversionDocumentReference
    object_key: str
    upload_url: str
    upload_fields: dict[str, str]
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class PreparedConversionBatch:
    snapshot: ConversionBatchSnapshot
    uploads: tuple[PreparedConversionBatchUpload, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class ConversionBatchService:
    repository: ConversionBatchRepository
    direct_upload_service: DirectUploadService
    queue_publisher: QueuePublisher
    batch_max_files: int = 12

    def create(
        self,
        *,
        identity: IdentityContext,
        files: list[ConversionBatchFile],
        idempotency_key: str,
    ) -> PreparedConversionBatch:
        if not 1 <= len(files) <= self.batch_max_files:
            raise ValueError(f"Conversion batch must contain between 1 and {self.batch_max_files} files.")
        normalized_idempotency_key = (idempotency_key or "").strip()
        if not normalized_idempotency_key or len(normalized_idempotency_key) > 200:
            raise ValueError("Conversion batch requires a valid idempotency key.")

        batch = ConversionBatch.create(
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
            files_count=len(files),
            idempotency_key=normalized_idempotency_key,
        )
        initially_prepared = [
            self.direct_upload_service.prepare(
                filename=item.filename,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                sha256_hex=item.sha256_hex,
                max_size_bytes=identity.max_upload_size_bytes,
            )
            for item in files
        ]
        jobs = [
            ConversionJob.create(
                batch_id=batch.batch_id,
                document=item.document,
                identity=identity,
                idempotency_key=f"{normalized_idempotency_key}:{index}",
            )
            for index, item in enumerate(initially_prepared)
        ]
        submission = self.repository.create(batch=batch, jobs=jobs)
        if not submission.created:
            self._validate_idempotent_files(submission.snapshot, files)
            initially_prepared = [
                self.direct_upload_service.prepare_reference(
                    document=record.job.document,
                    content_type=canonical_document_content_type(record.job.document.file_type),
                    max_size_bytes=identity.max_upload_size_bytes,
                )
                for record in submission.snapshot.jobs
            ]
            jobs = [record.job for record in submission.snapshot.jobs]

        uploads = tuple(
            PreparedConversionBatchUpload(
                job_id=job.job_id,
                document=prepared.document,
                object_key=prepared.object_key,
                upload_url=prepared.upload_url,
                upload_fields=prepared.upload_fields,
                expires_in_seconds=prepared.expires_in_seconds,
            )
            for job, prepared in zip(jobs, initially_prepared, strict=True)
        )
        return PreparedConversionBatch(
            snapshot=submission.snapshot,
            uploads=uploads,
            created=submission.created,
        )

    def submit(
        self,
        *,
        batch_id: str,
        identity: IdentityContext,
        trace_id: str,
    ) -> ConversionBatchSnapshot:
        snapshot = self.repository.get_for_owner(
            batch_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        if snapshot is None:
            raise KeyError(f"Conversion batch not found: {batch_id}")

        for record in snapshot.jobs:
            current = record
            if current.status == ConversionJobStatus.UPLOADING:
                self.direct_upload_service.verify_uploaded(current.job.document)
                current = self.repository.mark_uploaded(current.job.job_id)
            if current.status in {
                ConversionJobStatus.UPLOADED,
                ConversionJobStatus.SUBMITTED,
                ConversionJobStatus.RETRYING,
                ConversionJobStatus.QUEUED,
            }:
                self.repository.stage_for_queue(current.job.job_id, trace_id=trace_id)

        self.dispatch_pending_outbox()

        refreshed = self.repository.get_for_owner(
            batch_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        if refreshed is None:  # pragma: no cover - protected by owner-scoped lookup above
            raise KeyError(f"Conversion batch not found: {batch_id}")
        return refreshed

    def dispatch_pending_outbox(self, *, limit: int = 100) -> int:
        return dispatch_conversion_outbox(
            repository=self.repository,
            queue_publisher=self.queue_publisher,
            limit=limit,
        )

    @staticmethod
    def _validate_idempotent_files(snapshot: ConversionBatchSnapshot, files: list[ConversionBatchFile]) -> None:
        expected = [
            (item.filename, item.size_bytes, item.sha256_hex.lower())
            for item in files
        ]
        persisted = [
            (record.job.document.filename, record.job.document.size_bytes, record.job.document.sha256_hex)
            for record in snapshot.jobs
        ]
        if persisted != expected:
            raise ValueError("Idempotency key was already used with different conversion files.")


def dispatch_conversion_outbox(
    *,
    repository: ConversionBatchRepository,
    queue_publisher: QueuePublisher,
    limit: int = 100,
) -> int:
    published = 0
    for event in repository.list_pending_outbox(limit=limit):
        try:
            _publish_outbox_event(queue_publisher, event)
        except Exception as exc:
            repository.record_outbox_failure(event.event_id, message=str(exc))
            raise
        repository.mark_outbox_published(event.event_id)
        published += 1
    return published


def _publish_outbox_event(queue_publisher: QueuePublisher, event: ConversionOutboxEvent) -> None:
    queue_publisher.publish(
        job_id=event.job_id,
        batch_id=event.batch_id,
        trace_id=event.trace_id,
    )
