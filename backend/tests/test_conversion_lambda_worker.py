import io
import json
import logging
from datetime import datetime, timezone

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_batch import ConversionBatch, ConversionBatchStatus
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_job_repository import ConversionJobStatus
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.workers import conversion_lambda
from app.workers.conversion_lambda import ConversionLambdaProcessor, handle_sqs_event


class FakeDocumentStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def load(self, reference):
        return ingest_uploaded_document(reference.filename, b"date,description,amount\n2026-08-01,PIX,10.00\n")

    def delete(self, reference):
        self.deleted.append(reference.storage_key)


class SuccessfulPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def run_job(self, **_kwargs):
        self.calls += 1
        return ConversionPipelineResult.completed(
            payload={"processing_id": "an_worker123", "analysis": {"transactions_total": 1}}
        )


class TimeoutPipeline:
    def run_job(self, **_kwargs):
        raise TimeoutError("upstream timeout")


class InvalidPipeline:
    def run_job(self, **_kwargs):
        raise ValueError("invalid document")


def _queued_repository() -> tuple[InMemoryConversionBatchRepository, ConversionJob]:
    repository = InMemoryConversionBatchRepository()
    batch = ConversionBatch(
        batch_id="batch_worker",
        identity_type="user",
        identity_id="usr_123",
        files_count=1,
        idempotency_key="request-worker",
        status=ConversionBatchStatus.UPLOADING,
        created_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )
    job = ConversionJob.create(
        job_id="job_worker",
        batch_id=batch.batch_id,
        idempotency_key="request-worker:0",
        document=ConversionDocumentReference(
            storage_key="doc_0123456789abcdef01234567",
            filename="extrato.pdf",
            file_type="pdf",
            size_bytes=50,
            sha256_hex="a" * 64,
        ),
        identity=IdentityContext(identity_type="user", identity_id="usr_123", quota_limit=20),
    )
    repository.create(batch=batch, jobs=[job])
    repository.mark_uploaded(job.job_id)
    repository.mark_queued(job.job_id)
    return repository, job


def _event(job_id: str = "job_worker", *, receive_count: int = 1, message_id: str = "message-1") -> dict:
    return {
        "Records": [
            {
                "messageId": message_id,
                "body": (
                    '{"schema_version":1,"event_type":"conversion.requested",'
                    f'"job_id":"{job_id}","batch_id":"batch_worker","trace_id":"trace-123"}}'
                ),
                "attributes": {"ApproximateReceiveCount": str(receive_count)},
            }
        ]
    }


def test_lambda_processor_completes_job_persists_payload_and_deletes_source() -> None:
    repository, job = _queued_repository()
    store = FakeDocumentStore()
    pipeline = SuccessfulPipeline()
    processor = ConversionLambdaProcessor(repository=repository, document_store=store, pipeline=pipeline)

    response = handle_sqs_event(_event(), processor=processor, request_id="lambda-request-1")

    assert response == {"batchItemFailures": []}
    record = repository.get(job.job_id)
    assert record is not None
    assert record.status == ConversionJobStatus.COMPLETED
    assert record.result is not None
    assert record.result.analysis_id == "an_worker123"
    assert record.result.payload["analysis"]["transactions_total"] == 1
    assert store.deleted == [job.document.storage_key]

    handle_sqs_event(_event(message_id="message-duplicate"), processor=processor, request_id="lambda-request-2")
    assert pipeline.calls == 1


def test_lambda_partial_batch_response_retries_only_transient_failure() -> None:
    repository, job = _queued_repository()
    processor = ConversionLambdaProcessor(
        repository=repository,
        document_store=FakeDocumentStore(),
        pipeline=TimeoutPipeline(),
        max_receive_count=3,
    )

    response = handle_sqs_event(_event(), processor=processor, request_id="lambda-request-1")

    assert response == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}
    assert repository.get(job.job_id).status == ConversionJobStatus.RETRYING


def test_lambda_acknowledges_permanent_document_failure() -> None:
    repository, job = _queued_repository()
    store = FakeDocumentStore()
    processor = ConversionLambdaProcessor(repository=repository, document_store=store, pipeline=InvalidPipeline())

    response = handle_sqs_event(_event(), processor=processor, request_id="lambda-request-1")

    assert response == {"batchItemFailures": []}
    assert repository.get(job.job_id).status == ConversionJobStatus.FAILED
    assert store.deleted == [job.document.storage_key]


def test_lambda_rejects_malformed_message_to_dlq() -> None:
    repository, _job = _queued_repository()
    processor = ConversionLambdaProcessor(
        repository=repository,
        document_store=FakeDocumentStore(),
        pipeline=SuccessfulPipeline(),
    )
    event = {"Records": [{"messageId": "bad-message", "body": "not-json", "attributes": {}}]}

    response = handle_sqs_event(event, processor=processor, request_id="lambda-request-1")

    assert response == {"batchItemFailures": [{"itemIdentifier": "bad-message"}]}


def test_lambda_marks_exhausted_transient_failure_and_still_requests_redrive() -> None:
    repository, job = _queued_repository()
    store = FakeDocumentStore()
    processor = ConversionLambdaProcessor(
        repository=repository,
        document_store=store,
        pipeline=TimeoutPipeline(),
        max_receive_count=3,
    )

    response = handle_sqs_event(
        _event(receive_count=3),
        processor=processor,
        request_id="lambda-request-3",
    )

    assert response == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}
    assert repository.get(job.job_id).status == ConversionJobStatus.FAILED
    assert store.deleted == [job.document.storage_key]


def test_lambda_scheduled_event_dispatches_transactional_outbox(monkeypatch) -> None:
    repository, job = _queued_repository()
    repository.mark_running(job.job_id)
    repository.mark_retrying(job.job_id, code="TimeoutError", message="retry")
    repository.stage_for_queue(job.job_id, trace_id="trace-outbox")

    class Publisher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def publish(self, *, job_id, batch_id, trace_id):
            self.calls.append(job_id)
            return "message-outbox"

    publisher = Publisher()
    processor = ConversionLambdaProcessor(
        repository=repository,
        document_store=FakeDocumentStore(),
        pipeline=SuccessfulPipeline(),
    )
    monkeypatch.setattr(conversion_lambda, "_processor", processor)
    monkeypatch.setattr(conversion_lambda, "_outbox_publisher", publisher)
    context = type("Context", (), {"aws_request_id": "lambda-schedule-1"})()

    response = conversion_lambda.lambda_handler({"source": "aws.events"}, context)

    assert response == {"published": 1}
    assert publisher.calls == [job.job_id]
    assert repository.list_pending_outbox() == []


def test_worker_structured_info_logs_are_emitted_with_warning_root_logger() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root_logger = logging.getLogger()
    previous_root_level = root_logger.level
    previous_propagate = conversion_lambda.logger.propagate
    conversion_lambda.logger.addHandler(handler)
    conversion_lambda.logger.propagate = False
    root_logger.setLevel(logging.WARNING)

    try:
        conversion_lambda._log_json("worker_smoke", job_id="job_safe")
    finally:
        root_logger.setLevel(previous_root_level)
        conversion_lambda.logger.propagate = previous_propagate
        conversion_lambda.logger.removeHandler(handler)

    assert json.loads(stream.getvalue()) == {
        "event": "worker_smoke",
        "job_id": "job_safe",
    }


def test_build_lambda_processor_uses_worker_access_without_token_secret(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class WorkerAccess:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class AnalysisStorage:
        def __init__(self, **_kwargs) -> None:
            pass

    monkeypatch.setenv("DATABASE_URL", "postgresql://worker:test@database.example/gettdone")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "gettdone-conversions")
    monkeypatch.delenv("ACCESS_CONTROL_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(conversion_lambda, "PostgresConversionAccessService", WorkerAccess)
    monkeypatch.setattr(conversion_lambda, "S3AnalysisStorage", AnalysisStorage)

    processor = conversion_lambda.build_lambda_processor()

    assert isinstance(processor.pipeline.access_control_service, WorkerAccess)
    assert captured == {
        "database_url": "postgresql://worker:test@database.example/gettdone",
        "database_schema": "public",
        "db_pool_min_size": 0,
        "db_pool_max_size": 1,
    }
