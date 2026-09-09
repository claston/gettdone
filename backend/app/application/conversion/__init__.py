"""Compatibility exports loaded on demand, without initializing sibling services."""

from importlib import import_module

_EXPORTS: dict[str, tuple[str, str]] = {
    "ConversionArchitectureMode": ("app.application.conversion.conversion_runtime_config", "ConversionArchitectureMode"),
    "ConversionBatch": ("app.application.conversion.conversion_batch", "ConversionBatch"),
    "ConversionBatchFile": ("app.application.conversion.conversion_batch_service", "ConversionBatchFile"),
    "ConversionBatchRepository": ("app.application.conversion.conversion_batch_repository", "ConversionBatchRepository"),
    "ConversionBatchService": ("app.application.conversion.conversion_batch_service", "ConversionBatchService"),
    "ConversionBatchSnapshot": ("app.application.conversion.conversion_batch_repository", "ConversionBatchSnapshot"),
    "ConversionBatchStatus": ("app.application.conversion.conversion_batch", "ConversionBatchStatus"),
    "ConversionBatchSubmission": ("app.application.conversion.conversion_batch_repository", "ConversionBatchSubmission"),
    "ConversionCapacityController": ("app.application.conversion.conversion_capacity", "ConversionCapacityController"),
    "ConversionCapacityLease": ("app.application.conversion.conversion_capacity", "ConversionCapacityLease"),
    "ConversionDocumentReference": ("app.application.conversion.conversion_document_store", "ConversionDocumentReference"),
    "ConversionDocumentStore": ("app.application.conversion.conversion_document_store", "ConversionDocumentStore"),
    "ConversionExecutionHooks": ("app.application.conversion.conversion_job", "ConversionExecutionHooks"),
    "ConversionExecutionMode": ("app.application.conversion.conversion_runtime_config", "ConversionExecutionMode"),
    "ConversionJob": ("app.application.conversion.conversion_job", "ConversionJob"),
    "ConversionJobCleanupService": ("app.application.conversion.conversion_job_cleanup_service", "ConversionJobCleanupService"),
    "ConversionJobExecutor": ("app.application.conversion.conversion_job_executor", "ConversionJobExecutor"),
    "ConversionJobFactory": ("app.application.conversion.conversion_job_factory", "ConversionJobFactory"),
    "ConversionJobFailure": ("app.application.conversion.conversion_job_repository", "ConversionJobFailure"),
    "ConversionJobRecord": ("app.application.conversion.conversion_job_repository", "ConversionJobRecord"),
    "ConversionJobRepository": ("app.application.conversion.conversion_job_repository", "ConversionJobRepository"),
    "ConversionJobResultReference": ("app.application.conversion.conversion_job_repository", "ConversionJobResultReference"),
    "ConversionJobStatus": ("app.application.conversion.conversion_job_repository", "ConversionJobStatus"),
    "ConversionJobSubmission": ("app.application.conversion.conversion_job_repository", "ConversionJobSubmission"),
    "ConversionOutboxEvent": ("app.application.conversion.conversion_batch_repository", "ConversionOutboxEvent"),
    "ConversionPipelineResult": ("app.application.conversion.conversion_pipeline_result", "ConversionPipelineResult"),
    "ConversionPipelineStatus": ("app.application.conversion.conversion_pipeline_result", "ConversionPipelineStatus"),
    "ConversionRuntimeConfig": ("app.application.conversion.conversion_runtime_config", "ConversionRuntimeConfig"),
    "ConversionUploadMode": ("app.application.conversion.conversion_runtime_config", "ConversionUploadMode"),
    "ConvertDocumentResult": ("app.application.conversion.convert_document_result", "ConvertDocumentResult"),
    "ConvertDocumentStatus": ("app.application.conversion.convert_document_result", "ConvertDocumentStatus"),
    "DocumentExtractor": ("app.application.conversion.document_extractor", "DocumentExtractor"),
    "ExtractedDocument": ("app.application.conversion.document_extractor", "ExtractedDocument"),
    "FilesystemConversionDocumentStore": ("app.application.conversion.conversion_document_store", "FilesystemConversionDocumentStore"),
    "FilesystemConversionJobRepository": ("app.application.conversion.conversion_job_repository", "FilesystemConversionJobRepository"),
    "InMemoryConversionBatchRepository": ("app.application.conversion.conversion_batch_repository", "InMemoryConversionBatchRepository"),
    "InlineConversionJobExecutor": ("app.application.conversion.conversion_job_executor", "InlineConversionJobExecutor"),
    "MAX_CONVERSION_BATCH_FILES": ("app.application.conversion.conversion_batch", "MAX_CONVERSION_BATCH_FILES"),
    "ParsedBankStatement": ("app.application.conversion.statement_parser", "ParsedBankStatement"),
    "ParsedTransaction": ("app.application.conversion.statement_parser", "ParsedTransaction"),
    "PostgresConversionBatchRepository": (
        "app.application.conversion.postgres_conversion_batch_repository",
        "PostgresConversionBatchRepository",
    ),
    "PreparedConversionBatch": ("app.application.conversion.conversion_batch_service", "PreparedConversionBatch"),
    "PreparedConversionBatchUpload": ("app.application.conversion.conversion_batch_service", "PreparedConversionBatchUpload"),
    "PreparedS3Upload": ("app.application.conversion.s3_direct_upload_service", "PreparedS3Upload"),
    "S3ConversionDocumentStore": ("app.application.conversion.conversion_document_store", "S3ConversionDocumentStore"),
    "S3DirectUploadService": ("app.application.conversion.s3_direct_upload_service", "S3DirectUploadService"),
    "SUPPORTED_DOCUMENT_EXTENSIONS": ("app.application.conversion.uploaded_document", "SUPPORTED_DOCUMENT_EXTENSIONS"),
    "SqsConversionQueuePublisher": ("app.application.conversion.sqs_conversion_queue", "SqsConversionQueuePublisher"),
    "StatementParser": ("app.application.conversion.statement_parser", "StatementParser"),
    "UploadedDocument": ("app.application.conversion.uploaded_document", "UploadedDocument"),
    "UploadedDocumentStage": ("app.application.conversion.uploaded_document", "UploadedDocumentStage"),
    "dispatch_conversion_outbox": ("app.application.conversion.conversion_batch_service", "dispatch_conversion_outbox"),
    "ingest_uploaded_document": ("app.application.conversion.uploaded_document", "ingest_uploaded_document"),
    "resolve_conversion_batch_status": ("app.application.conversion.conversion_batch", "resolve_conversion_batch_status"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    export = _EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = export
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
