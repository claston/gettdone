from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, time
from typing import Protocol

from app.adapters.conversion.access import PostgresConversionAccessService
from app.adapters.conversion.document_store import S3ConversionDocumentStore
from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository
from app.adapters.conversion.sqs_queue import SqsConversionQueuePublisher
from app.application.conversion.contracts.batches import ConversionBatchRepository
from app.application.conversion.contracts.documents import ConversionDocumentStore
from app.application.conversion.contracts.jobs import ConversionJobResultReference, ConversionJobStatus
from app.application.conversion.conversion_batch_service import dispatch_conversion_outbox
from app.application.conversion.conversion_job import ConversionExecutionHooks
from app.application.conversion.conversion_pipeline_result import ConversionPipelineStatus
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.s3_analysis_storage import S3AnalysisStorage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ConversionPipeline(Protocol):
    def run_job(self, **kwargs): ...


class RetryableConversionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConversionLambdaProcessor:
    repository: ConversionBatchRepository
    document_store: ConversionDocumentStore
    pipeline: ConversionPipeline
    max_receive_count: int = 5
    lease_seconds: int = 900
    results_s3_prefix: str = "conversion/results"

    def process(
        self,
        *,
        job_id: str,
        expected_batch_id: str,
        trace_id: str,
        receive_count: int,
        lease_owner: str,
    ) -> None:
        started_at = monotonic()
        record = self.repository.get(job_id)
        if record is None or record.job.batch_id != expected_batch_id:
            raise ValueError("Conversion queue message does not match a persisted job.")
        if record.status in {
            ConversionJobStatus.COMPLETED,
            ConversionJobStatus.FAILED,
            ConversionJobStatus.EXPIRED,
        }:
            self._log_outcome(record.job, trace_id=trace_id, outcome="already_terminal", started_at=started_at)
            return

        claimed = self.repository.claim_for_processing(
            job_id,
            lease_owner=lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            refreshed = self.repository.get(job_id)
            if refreshed is not None and refreshed.status in {
                ConversionJobStatus.COMPLETED,
                ConversionJobStatus.FAILED,
                ConversionJobStatus.EXPIRED,
            }:
                return
            raise RetryableConversionError("Conversion job is already being processed.")

        should_delete_source = False
        try:
            document = self.document_store.load(claimed.job.document)
            result = self.pipeline.run_job(
                job=claimed.job,
                document=document,
                hooks=ConversionExecutionHooks(),
            )
            payload = result.payload or {}
            if result.status == ConversionPipelineStatus.COMPLETED:
                analysis_id = str(payload.get("processing_id") or payload.get("analysis_id") or "").strip()
                if not analysis_id:
                    raise RuntimeError("Completed conversion result does not contain an analysis id.")
                result_prefix = f"{self.results_s3_prefix.strip().strip('/')}/{analysis_id}"
                self.repository.mark_completed(
                    job_id,
                    result=ConversionJobResultReference(
                        analysis_id=analysis_id,
                        payload=payload,
                        s3_prefix=result_prefix,
                    ),
                )
                should_delete_source = True
                self._log_outcome(claimed.job, trace_id=trace_id, outcome="completed", started_at=started_at)
                return

            failure_code = result.rejection_reason or "conversion_failed"
            self.repository.mark_failed(job_id, code=failure_code, message=result.message)
            should_delete_source = True
            self._log_outcome(
                claimed.job,
                trace_id=trace_id,
                outcome="rejected" if result.status == ConversionPipelineStatus.REJECTED else "failed",
                started_at=started_at,
                error_code=failure_code,
            )
        except Exception as exc:
            retryable = is_retryable_conversion_error(exc)
            if retryable and receive_count < max(1, self.max_receive_count):
                self.repository.mark_retrying(job_id, code=type(exc).__name__, message=str(exc))
                self._log_outcome(
                    claimed.job,
                    trace_id=trace_id,
                    outcome="retrying",
                    started_at=started_at,
                    error_code=type(exc).__name__,
                )
                raise RetryableConversionError(str(exc)) from exc

            self.repository.mark_failed(job_id, code=type(exc).__name__, message=str(exc))
            should_delete_source = True
            self._log_outcome(
                claimed.job,
                trace_id=trace_id,
                outcome="failed",
                started_at=started_at,
                error_code=type(exc).__name__,
            )
            if retryable:
                raise RetryableConversionError(str(exc)) from exc
        finally:
            if should_delete_source:
                try:
                    self.document_store.delete(claimed.job.document)
                except Exception as exc:  # cleanup is also covered by S3 lifecycle
                    _log_json(
                        "conversion_source_cleanup_failed",
                        job_id=claimed.job.job_id,
                        batch_id=claimed.job.batch_id,
                        error_class=type(exc).__name__,
                    )

    @staticmethod
    def _log_outcome(job, *, trace_id: str, outcome: str, started_at: float, error_code: str | None = None) -> None:
        duration_ms = int((monotonic() - started_at) * 1000)
        _log_json(
            "conversion_worker_result",
            job_id=job.job_id,
            batch_id=job.batch_id,
            trace_id=trace_id,
            outcome=outcome,
            duration_ms=duration_ms,
            error_code=error_code,
        )
        _log_metric(outcome=outcome, duration_ms=duration_ms)


def handle_sqs_event(
    event: dict[str, object],
    *,
    processor: ConversionLambdaProcessor,
    request_id: str,
) -> dict[str, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    for raw_record in event.get("Records") or []:
        record = raw_record if isinstance(raw_record, dict) else {}
        message_id = str(record.get("messageId") or "unknown-message")
        try:
            body = json.loads(str(record.get("body") or ""))
            if not isinstance(body, dict):
                raise ValueError("Conversion queue body must be an object.")
            if body.get("schema_version") != 1 or body.get("event_type") != "conversion.requested":
                raise ValueError("Unsupported conversion queue message contract.")
            attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            receive_count = max(1, int(attributes.get("ApproximateReceiveCount", "1")))
            processor.process(
                job_id=str(body.get("job_id") or ""),
                expected_batch_id=str(body.get("batch_id") or ""),
                trace_id=str(body.get("trace_id") or ""),
                receive_count=receive_count,
                lease_owner=f"{request_id}:{message_id}",
            )
        except Exception as exc:
            failures.append({"itemIdentifier": message_id})
            _log_json(
                "conversion_worker_message_failed",
                message_id=message_id,
                request_id=request_id,
                error_class=type(exc).__name__,
            )
    return {"batchItemFailures": failures}


_processor: ConversionLambdaProcessor | None = None
_outbox_publisher: SqsConversionQueuePublisher | None = None


def lambda_handler(event, context):
    global _outbox_publisher, _processor
    if _processor is None:
        _processor = build_lambda_processor()
    if not event.get("Records"):
        if str(event.get("source") or "") != "aws.events" and event.get("action") != "dispatch_outbox":
            raise ValueError("Unsupported conversion Lambda event.")
        if _outbox_publisher is None:
            _outbox_publisher = SqsConversionQueuePublisher(
                queue_url=_required_env("CONVERSION_SQS_QUEUE_URL"),
                region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            )
        published = dispatch_conversion_outbox(
            repository=_processor.repository,
            queue_publisher=_outbox_publisher,
            limit=int(os.getenv("CONVERSION_OUTBOX_DISPATCH_LIMIT", "100")),
        )
        _log_json("conversion_outbox_dispatched", request_id=str(getattr(context, "aws_request_id", "")), published=published)
        return {"published": published}
    return handle_sqs_event(
        event,
        processor=_processor,
        request_id=str(getattr(context, "aws_request_id", "lambda-request")),
    )


def build_lambda_processor() -> ConversionLambdaProcessor:
    database_url = _required_env("DATABASE_URL")
    bucket = _required_env("CONVERSION_S3_BUCKET")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    database_schema = os.getenv("DATABASE_SCHEMA", "public")
    active_ttl = int(os.getenv("CONVERSION_JOB_ACTIVE_TTL_SECONDS", "86400"))
    terminal_ttl = int(os.getenv("CONVERSION_JOB_TERMINAL_TTL_SECONDS", "86400"))
    repository = PostgresConversionBatchRepository(
        database_url=database_url,
        database_schema=database_schema,
        active_ttl_seconds=active_ttl,
        terminal_ttl_seconds=terminal_ttl,
    )
    document_store = S3ConversionDocumentStore(
        bucket=bucket,
        prefix=os.getenv("CONVERSION_S3_PREFIX", "conversion/jobs"),
        region=region,
        server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
        kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
    )
    analysis_storage = S3AnalysisStorage(
        root_dir=Path("/tmp/gettdone/analyses"),
        ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
        bucket=bucket,
        prefix=os.getenv("CONVERSION_RESULTS_S3_PREFIX", "conversion/results"),
        region=region,
        server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
        kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
    )
    access_control_service = PostgresConversionAccessService(
        database_url=database_url,
        database_schema=database_schema,
        db_pool_min_size=1,
        db_pool_max_size=int(os.getenv("CONVERSION_LAMBDA_DB_POOL_MAX_SIZE", "1")),
    )
    pipeline = DocumentConversionPipeline(
        report_service=ReportService(storage=analysis_storage),
        access_control_service=access_control_service,
        processing_pipeline=build_default_conversion_pipeline(),
        analysis_repository=analysis_storage,
    )
    return ConversionLambdaProcessor(
        repository=repository,
        document_store=document_store,
        pipeline=pipeline,
        max_receive_count=int(os.getenv("CONVERSION_SQS_MAX_RECEIVE_COUNT", "5")),
        lease_seconds=int(os.getenv("CONVERSION_JOB_LEASE_SECONDS", "900")),
        results_s3_prefix=os.getenv("CONVERSION_RESULTS_S3_PREFIX", "conversion/results"),
    )


def is_retryable_conversion_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    retryable_names = {
        "ClientConnectionError",
        "ConnectionClosedError",
        "EndpointConnectionError",
        "OperationalError",
        "ReadTimeoutError",
        "ServiceUnavailableError",
    }
    if type(exc).__name__ in retryable_names:
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
        code = str(error.get("Code") or "")
        return code in {"429", "InternalError", "RequestTimeout", "ServiceUnavailable", "SlowDown", "Throttling"}
    return False


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required by the conversion Lambda.")
    return value


def _log_json(event: str, **fields: object) -> None:
    payload = {"event": event, **{key: value for key, value in fields.items() if value is not None}}
    logger.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _log_metric(*, outcome: str, duration_ms: int) -> None:
    payload = {
        "_aws": {
            "Timestamp": int(time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "GettDone/Conversions",
                    "Dimensions": [["Service", "Outcome"]],
                    "Metrics": [
                        {"Name": "Jobs", "Unit": "Count"},
                        {"Name": "Duration", "Unit": "Milliseconds"},
                    ],
                }
            ],
        },
        "Service": "lambda-worker",
        "Outcome": outcome,
        "Jobs": 1,
        "Duration": duration_ms,
    }
    logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
