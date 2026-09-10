"""Lazy compatibility facade for application services.

Concrete application imports must not initialize unrelated web or
administrative surfaces. Existing facade imports remain compatible while the
callers migrate incrementally to explicit modules.
"""

from importlib import import_module

_EXPORTS: dict[str, tuple[str, str]] = {
    "AccessControlService": ("app.application.access_control", "AccessControlService"),
    "AnalysisAccessDeniedError": ("app.application.errors", "AnalysisAccessDeniedError"),
    "AnalysisEditConflictError": ("app.application.errors", "AnalysisEditConflictError"),
    "AnalysisNotFoundError": ("app.application.errors", "AnalysisNotFoundError"),
    "AnalysisRepository": ("app.application.repositories", "AnalysisRepository"),
    "ContactAttachment": ("app.application.contact_service", "ContactAttachment"),
    "ContactDeliveryError": ("app.application.errors", "ContactDeliveryError"),
    "ContactDeliveryResult": ("app.application.contact_service", "ContactDeliveryResult"),
    "ContactMessage": ("app.application.contact_service", "ContactMessage"),
    "ContactProviderNotConfiguredError": ("app.application.errors", "ContactProviderNotConfiguredError"),
    "ContactService": ("app.application.contact_service", "ContactService"),
    "ConversionCapacityController": (
        "app.application.conversion.conversion_capacity",
        "ConversionCapacityController",
    ),
    "ConversionCapacityLease": ("app.application.conversion.conversion_capacity", "ConversionCapacityLease"),
    "ConversionDocumentReference": (
        "app.application.conversion.conversion_document_store",
        "ConversionDocumentReference",
    ),
    "ConversionDocumentStore": (
        "app.application.conversion.conversion_document_store",
        "ConversionDocumentStore",
    ),
    "ConversionExecutionHooks": ("app.application.conversion.conversion_job", "ConversionExecutionHooks"),
    "ConversionJob": ("app.application.conversion.conversion_job", "ConversionJob"),
    "ConversionJobCleanupService": (
        "app.application.conversion.conversion_job_cleanup_service",
        "ConversionJobCleanupService",
    ),
    "ConversionJobExecutor": ("app.application.conversion.conversion_job_executor", "ConversionJobExecutor"),
    "ConversionJobFactory": ("app.application.conversion.conversion_job_factory", "ConversionJobFactory"),
    "ConversionJobFailure": (
        "app.application.conversion.conversion_job_repository",
        "ConversionJobFailure",
    ),
    "ConversionJobRecord": ("app.application.conversion.contracts.jobs", "ConversionJobRecord"),
    "ConversionJobRepository": (
        "app.application.conversion.conversion_job_repository",
        "ConversionJobRepository",
    ),
    "ConversionJobResultReference": (
        "app.application.conversion.conversion_job_repository",
        "ConversionJobResultReference",
    ),
    "ConversionJobStatus": ("app.application.conversion.contracts.jobs", "ConversionJobStatus"),
    "ConversionJobSubmission": (
        "app.application.conversion.conversion_job_repository",
        "ConversionJobSubmission",
    ),
    "ConversionPipelineResult": (
        "app.application.conversion.conversion_pipeline_result",
        "ConversionPipelineResult",
    ),
    "ConversionPipelineStatus": (
        "app.application.conversion.conversion_pipeline_result",
        "ConversionPipelineStatus",
    ),
    "ConversionService": ("app.application.conversion_service", "ConversionService"),
    "ConvertDocumentResult": ("app.application.conversion.convert_document_result", "ConvertDocumentResult"),
    "ConvertDocumentStatus": ("app.application.conversion.convert_document_result", "ConvertDocumentStatus"),
    "ConvertDocumentUseCase": ("app.application.conversion.convert_document_use_case", "ConvertDocumentUseCase"),
    "DocumentConversionPipeline": (
        "app.application.conversion.document_conversion_pipeline",
        "DocumentConversionPipeline",
    ),
    "DocumentExtractor": ("app.application.conversion.document_extractor", "DocumentExtractor"),
    "DocumentPreflightPolicy": (
        "app.application.conversion.document_preflight_service",
        "DocumentPreflightPolicy",
    ),
    "DocumentPreflightResult": (
        "app.application.conversion.document_preflight_service",
        "DocumentPreflightResult",
    ),
    "DocumentPreflightService": (
        "app.application.conversion.document_preflight_service",
        "DocumentPreflightService",
    ),
    "EmailVerificationRateLimitedError": ("app.application.errors", "EmailVerificationRateLimitedError"),
    "EmailVerificationRequiredError": ("app.application.errors", "EmailVerificationRequiredError"),
    "ExtractedDocument": ("app.application.conversion.document_extractor", "ExtractedDocument"),
    "FileTooLargeError": ("app.application.errors", "FileTooLargeError"),
    "FilesystemConversionDocumentStore": (
        "app.application.conversion.conversion_document_store",
        "FilesystemConversionDocumentStore",
    ),
    "FilesystemConversionJobRepository": (
        "app.application.conversion.conversion_job_repository",
        "FilesystemConversionJobRepository",
    ),
    "GoogleOAuthAccountNotFoundError": ("app.application.errors", "GoogleOAuthAccountNotFoundError"),
    "GoogleOAuthCallbackResult": ("app.application.google_oauth_service", "GoogleOAuthCallbackResult"),
    "GoogleOAuthConfig": ("app.application.google_oauth_service", "GoogleOAuthConfig"),
    "GoogleOAuthExchangeError": ("app.application.errors", "GoogleOAuthExchangeError"),
    "GoogleOAuthNotConfiguredError": ("app.application.errors", "GoogleOAuthNotConfiguredError"),
    "GoogleOAuthService": ("app.application.google_oauth_service", "GoogleOAuthService"),
    "GoogleOAuthStateError": ("app.application.errors", "GoogleOAuthStateError"),
    "InlineConversionJobExecutor": (
        "app.application.conversion.conversion_job_executor",
        "InlineConversionJobExecutor",
    ),
    "InvalidCredentialsError": ("app.application.errors", "InvalidCredentialsError"),
    "InvalidEmailVerificationTokenError": (
        "app.application.errors",
        "InvalidEmailVerificationTokenError",
    ),
    "InvalidFileContentError": ("app.application.errors", "InvalidFileContentError"),
    "InvalidSessionTokenError": ("app.application.errors", "InvalidSessionTokenError"),
    "InvalidUserTokenError": ("app.application.errors", "InvalidUserTokenError"),
    "MaxPagesPerFileExceededError": ("app.application.errors", "MaxPagesPerFileExceededError"),
    "ParsedBankStatement": ("app.application.conversion.statement_parser", "ParsedBankStatement"),
    "ParsedTransaction": ("app.application.conversion.statement_parser", "ParsedTransaction"),
    "PersistedConversionResult": (
        "app.application.conversion.persisted_conversion_result",
        "PersistedConversionResult",
    ),
    "QuotaConsumptionResult": (
        "app.application.conversion.quota_validator_service",
        "QuotaConsumptionResult",
    ),
    "QuotaExceededError": ("app.application.errors", "QuotaExceededError"),
    "QuotaValidatorService": ("app.application.conversion.quota_validator_service", "QuotaValidatorService"),
    "ReportRepository": ("app.application.repositories", "ReportRepository"),
    "ReportService": ("app.application.report_service", "ReportService"),
    "ReusedSessionTokenError": ("app.application.errors", "ReusedSessionTokenError"),
    "S3AnalysisStorage": ("app.application.s3_analysis_storage", "S3AnalysisStorage"),
    "S3ConversionDocumentStore": (
        "app.application.conversion.conversion_document_store",
        "S3ConversionDocumentStore",
    ),
    "SmtpContactService": ("app.application.contact_service", "SmtpContactService"),
    "StatementParser": ("app.application.conversion.statement_parser", "StatementParser"),
    "SUPPORTED_DOCUMENT_EXTENSIONS": (
        "app.application.conversion.uploaded_document",
        "SUPPORTED_DOCUMENT_EXTENSIONS",
    ),
    "TempAnalysisStorage": ("app.application.storage_service", "TempAnalysisStorage"),
    "UnsupportedFileTypeError": ("app.application.errors", "UnsupportedFileTypeError"),
    "UploadedDocument": ("app.application.conversion.uploaded_document", "UploadedDocument"),
    "UploadedDocumentStage": ("app.application.conversion.uploaded_document", "UploadedDocumentStage"),
    "UserAlreadyExistsError": ("app.application.errors", "UserAlreadyExistsError"),
    "build_analyze_response": ("app.application.conversion.result_payload", "build_analyze_response"),
    "build_convert_response_payload": (
        "app.application.analysis_response_builder",
        "build_convert_response_payload",
    ),
    "build_default_conversion_pipeline": (
        "app.application.default_conversion_pipeline",
        "build_default_conversion_pipeline",
    ),
    "build_ofx_statement": ("app.application.ofx_writer", "build_ofx_statement"),
    "classify_reconciliation_rows": (
        "app.application.reconcile_status_engine",
        "classify_reconciliation_rows",
    ),
    "generate_reconciliation_problems": (
        "app.application.reconcile_problem_engine",
        "generate_reconciliation_problems",
    ),
    "ingest_uploaded_document": ("app.application.conversion.uploaded_document", "ingest_uploaded_document"),
    "match_exact_then_date_tolerance_then_description_similarity_1to1": (
        "app.application.ledger_match_engine",
        "match_exact_then_date_tolerance_then_description_similarity_1to1",
    ),
    "parse_bank_statement_rows": ("app.application.parsers.bank_statement", "parse_bank_statement_rows"),
    "parse_operational_sheet_rows": ("app.application.parsers.sheet", "parse_operational_sheet_rows"),
    "persist_and_build_analyze_response": (
        "app.application.analysis_response_builder",
        "persist_and_build_analyze_response",
    ),
    "persist_conversion_result": ("app.application.conversion.result_persistence", "persist_conversion_result"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    export = _EXPORTS.get(name)
    if export is not None:
        module_name, attribute_name = export
        value = getattr(import_module(module_name), attribute_name)
        globals()[name] = value
        return value

    submodule_name = f"{__name__}.{name}"
    try:
        value = import_module(submodule_name)
    except ModuleNotFoundError as exc:
        if exc.name != submodule_name:
            raise
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
