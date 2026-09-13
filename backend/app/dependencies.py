import os
from pathlib import Path

from fastapi import Depends

from app.adapters.conversion.canonical_layout_store import build_s3_canonical_layout_capture_service
from app.adapters.conversion.direct_upload import S3DirectUploadService
from app.adapters.conversion.memory_batches import InMemoryConversionBatchRepository
from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository
from app.adapters.conversion.sqs_queue import SqsConversionQueuePublisher
from app.application import (
    AccessControlService,
    ContactService,
    ConversionCapacityController,
    ConversionDocumentStore,
    ConversionJobCleanupService,
    ConversionJobExecutor,
    ConversionJobFactory,
    ConversionJobRepository,
    ConvertDocumentUseCase,
    DocumentConversionPipeline,
    DocumentPreflightService,
    FilesystemConversionDocumentStore,
    FilesystemConversionJobRepository,
    GoogleOAuthConfig,
    GoogleOAuthService,
    InlineConversionJobExecutor,
    QuotaValidatorService,
    ReportService,
    S3AnalysisStorage,
    S3ConversionDocumentStore,
    SmtpContactService,
    TempAnalysisStorage,
)
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.canonical_layout_capture import CanonicalLayoutCaptureService
from app.application.conversion.contracts.batches import ConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_runtime_config import (
    ConversionArchitectureMode,
    ConversionExecutionMode,
    ConversionRuntimeConfig,
)
from app.application.conversion_pipeline import ConversionPipeline
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.repositories import AnalysisRepository
from app.security_baseline import is_production_env, read_bool_env

_backend_root = Path(__file__).resolve().parents[1]


def _build_conversion_document_store(*, backend_root: Path) -> ConversionDocumentStore:
    mode = os.getenv("CONVERSION_DOCUMENT_STORE", "filesystem").strip().lower()
    if mode == "filesystem":
        return FilesystemConversionDocumentStore(
            root_dir=backend_root / "tmp" / "conversion_jobs" / "documents",
        )
    if mode == "s3":
        return S3ConversionDocumentStore(
            bucket=os.getenv("CONVERSION_S3_BUCKET", ""),
            prefix=os.getenv("CONVERSION_S3_PREFIX", "conversion/jobs"),
            region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
            kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
        )
    raise RuntimeError("CONVERSION_DOCUMENT_STORE must be 'filesystem' or 's3'.")


def _build_analysis_storage(*, backend_root: Path):
    mode = os.getenv("ANALYSIS_STORAGE", "filesystem").strip().lower()
    ttl_seconds = int(os.getenv("ANALYSIS_TTL_SECONDS", "86400"))
    if mode == "filesystem":
        return TempAnalysisStorage(
            root_dir=backend_root / "tmp" / "analyses",
            ttl_seconds=ttl_seconds,
        )
    if mode == "s3":
        return S3AnalysisStorage(
            root_dir=backend_root / "tmp" / "analyses",
            ttl_seconds=ttl_seconds,
            bucket=os.getenv("CONVERSION_S3_BUCKET", ""),
            prefix=os.getenv("CONVERSION_RESULTS_S3_PREFIX", "conversion/results"),
            region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
            kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
        )
    raise RuntimeError("ANALYSIS_STORAGE must be 'filesystem' or 's3'.")


_storage = _build_analysis_storage(backend_root=_backend_root)
_conversion_processing_pipeline = build_default_conversion_pipeline()
_report_service = ReportService(storage=_storage)
_document_preflight_service = DocumentPreflightService()
_conversion_document_store = _build_conversion_document_store(backend_root=_backend_root)
_conversion_job_repository: ConversionJobRepository | None = None
_conversion_job_cleanup_service: ConversionJobCleanupService | None = None
_access_control_service: AccessControlService | None = None
_contact_service: ContactService | None = None
_contact_form_service: ContactService | SmtpContactService | None = None
_google_oauth_service: GoogleOAuthService | None = None
_conversion_capacity_controller: ConversionCapacityController | None = None
_conversion_batch_repository: ConversionBatchRepository | None = None
_conversion_batch_service: ConversionBatchService | None = None
_async_conversion_report_service: ReportService | None = None
_canonical_layout_capture_service: CanonicalLayoutCaptureService | None = None


class _DisabledConversionQueuePublisher:
    def publish(self, *, job_id: str, batch_id: str, trace_id: str) -> str:
        _ = (job_id, batch_id, trace_id)
        raise RuntimeError("Conversion SQS publishing is disabled by the active runtime fallback.")


def _resolve_access_control_state_file() -> Path:
    configured_dir = os.getenv("ACCESS_CONTROL_STATE_DIR", "").strip()
    if configured_dir:
        return Path(configured_dir) / "state.json"

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "gettdone" / "access_control" / "state.json"

    return _backend_root / "tmp" / "access_control" / "state.json"


def get_conversion_processing_pipeline() -> ConversionPipeline:
    return _conversion_processing_pipeline

def get_legacy_conversion_runner():
    return None


def get_report_service() -> ReportService:
    return _report_service


def get_analysis_repository() -> AnalysisRepository:
    return _storage


def get_document_preflight_service() -> DocumentPreflightService:
    return _document_preflight_service


def get_conversion_document_store() -> ConversionDocumentStore:
    return _conversion_document_store


def get_conversion_job_repository() -> ConversionJobRepository:
    global _conversion_job_repository
    if _conversion_job_repository is None:
        runtime = get_conversion_runtime_config()
        if runtime.architecture_mode == ConversionArchitectureMode.ASYNC_AWS:
            _conversion_job_repository = get_conversion_batch_repository()
        else:
            _conversion_job_repository = FilesystemConversionJobRepository(
                root_dir=_backend_root / "tmp" / "conversion_jobs" / "registry",
                active_ttl_seconds=int(os.getenv("CONVERSION_JOB_ACTIVE_TTL_SECONDS", "86400")),
                terminal_ttl_seconds=int(os.getenv("CONVERSION_JOB_TERMINAL_TTL_SECONDS", "86400")),
            )
    return _conversion_job_repository


def get_conversion_job_cleanup_service() -> ConversionJobCleanupService:
    global _conversion_job_cleanup_service
    if _conversion_job_cleanup_service is None:
        _conversion_job_cleanup_service = ConversionJobCleanupService(
            job_repository=get_conversion_job_repository(),
            document_store=get_conversion_document_store(),
        )
    return _conversion_job_cleanup_service


def get_conversion_runtime_config() -> ConversionRuntimeConfig:
    return ConversionRuntimeConfig.from_mapping(os.environ)


def get_async_conversion_rollout_policy() -> AsyncConversionRolloutPolicy:
    return AsyncConversionRolloutPolicy.from_mapping(os.environ)


def get_async_conversion_report_service() -> ReportService | None:
    global _async_conversion_report_service
    runtime = get_conversion_runtime_config()
    rollout_policy = get_async_conversion_rollout_policy()
    if runtime.architecture_mode != ConversionArchitectureMode.ASYNC_AWS and not rollout_policy.enabled:
        return None
    if os.getenv("ANALYSIS_STORAGE", "filesystem").strip().lower() == "s3":
        return _report_service
    if _async_conversion_report_service is None:
        _async_conversion_report_service = ReportService(
            storage=S3AnalysisStorage(
                root_dir=_backend_root / "tmp" / "async_analyses",
                ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
                bucket=os.getenv("CONVERSION_S3_BUCKET", ""),
                prefix=os.getenv("CONVERSION_RESULTS_S3_PREFIX", "conversion/results"),
                region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
                server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
                kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
            )
        )
    return _async_conversion_report_service


def get_conversion_batch_repository() -> ConversionBatchRepository:
    global _conversion_batch_repository
    if _conversion_batch_repository is not None:
        return _conversion_batch_repository

    mode = os.getenv("CONVERSION_BATCH_REPOSITORY", "postgres").strip().lower()
    if mode == "memory":
        if is_production_env():
            raise RuntimeError("CONVERSION_BATCH_REPOSITORY=memory is not allowed in production.")
        _conversion_batch_repository = InMemoryConversionBatchRepository(
            ttl_seconds=int(os.getenv("CONVERSION_JOB_ACTIVE_TTL_SECONDS", "86400")),
        )
        return _conversion_batch_repository
    if mode != "postgres":
        raise RuntimeError("CONVERSION_BATCH_REPOSITORY must be 'postgres' or 'memory'.")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for asynchronous conversion batches.")
    _conversion_batch_repository = PostgresConversionBatchRepository(
        database_url=database_url,
        database_schema=os.getenv("DATABASE_SCHEMA", "public"),
        active_ttl_seconds=int(os.getenv("CONVERSION_JOB_ACTIVE_TTL_SECONDS", "86400")),
        terminal_ttl_seconds=int(os.getenv("CONVERSION_JOB_TERMINAL_TTL_SECONDS", "86400")),
    )
    return _conversion_batch_repository


def get_conversion_batch_service() -> ConversionBatchService | None:
    global _conversion_batch_service
    runtime = get_conversion_runtime_config()
    rollout_policy = get_async_conversion_rollout_policy()
    if runtime.architecture_mode != ConversionArchitectureMode.ASYNC_AWS and not rollout_policy.enabled:
        return None
    if _conversion_batch_service is None:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        direct_upload_service = S3DirectUploadService(
            bucket=os.getenv("CONVERSION_S3_BUCKET", ""),
            prefix=os.getenv("CONVERSION_S3_PREFIX", "conversion/jobs"),
            region=region,
            expires_in_seconds=int(os.getenv("CONVERSION_DIRECT_UPLOAD_TTL_SECONDS", "900")),
            server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
            kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
        )
        if runtime.execution_mode == ConversionExecutionMode.SQS_LAMBDA or rollout_policy.enabled:
            queue_publisher = SqsConversionQueuePublisher(
                queue_url=os.getenv("CONVERSION_SQS_QUEUE_URL", ""),
                region=region,
            )
        else:
            queue_publisher = _DisabledConversionQueuePublisher()
        _conversion_batch_service = ConversionBatchService(
            repository=get_conversion_batch_repository(),
            direct_upload_service=direct_upload_service,
            queue_publisher=queue_publisher,
            batch_max_files=runtime.batch_max_files,
        )
    return _conversion_batch_service


def get_access_control_service() -> AccessControlService:
    global _access_control_service
    if _access_control_service is None:
        token_secret = os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "").strip() or "dev-access-control-secret"
        anonymous_quota_limit = int(os.getenv("ANONYMOUS_QUOTA_LIMIT", "3"))
        unlimited_anon_quota = read_bool_env("UNLIMITED_ANON_QUOTA", default=False)
        if is_production_env() and token_secret == "dev-access-control-secret":
            raise RuntimeError("ACCESS_CONTROL_TOKEN_SECRET must be configured in production.")
        if is_production_env() and unlimited_anon_quota:
            raise RuntimeError("UNLIMITED_ANON_QUOTA must be false in production.")
        if unlimited_anon_quota:
            anonymous_quota_limit = 9999
        configured_admin_emails = {
            item.strip().lower()
            for item in os.getenv("ADMIN_EMAILS", "").split(",")
            if item.strip()
        }
        _access_control_service = AccessControlService(
            state_file=_resolve_access_control_state_file(),
            token_secret=token_secret,
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            database_schema=os.getenv("DATABASE_SCHEMA", "public").strip(),
            admin_emails=configured_admin_emails,
            anonymous_quota_limit=anonymous_quota_limit,
            quota_window_days=int(os.getenv("QUOTA_WINDOW_DAYS", "7")),
            session_access_token_ttl_seconds=int(os.getenv("SESSION_ACCESS_TOKEN_TTL_SECONDS", "900")),
            session_refresh_token_ttl_seconds=int(os.getenv("SESSION_REFRESH_TOKEN_TTL_SECONDS", "1209600")),
            active_plan_cache_ttl_seconds=int(os.getenv("ACTIVE_PLAN_CACHE_TTL_SECONDS", "20")),
            db_connect_retry_attempts=int(os.getenv("DB_CONNECT_RETRY_ATTEMPTS", "3")),
            db_connect_retry_base_ms=int(os.getenv("DB_CONNECT_RETRY_BASE_MS", "200")),
            db_pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "3")),
            db_pool_timeout_seconds=float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "5")),
            email_verification_required=read_bool_env("AUTH_EMAIL_VERIFICATION_REQUIRED", default=False),
            email_verification_ttl_seconds=int(os.getenv("AUTH_EMAIL_VERIFICATION_TTL_SECONDS", "3600")),
            email_verification_resend_cooldown_seconds=int(
                os.getenv("AUTH_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60")
            ),
            email_verification_daily_limit=int(os.getenv("AUTH_EMAIL_VERIFICATION_DAILY_LIMIT", "5")),
        )
    return _access_control_service


def close_access_control_service() -> None:
    global _access_control_service
    if _access_control_service is not None:
        _access_control_service.close()
        _access_control_service = None


def get_conversion_capacity_controller() -> ConversionCapacityController:
    global _conversion_capacity_controller
    if _conversion_capacity_controller is None:
        _conversion_capacity_controller = ConversionCapacityController.from_env()
    return _conversion_capacity_controller


def close_conversion_capacity_controller() -> None:
    global _conversion_capacity_controller
    if _conversion_capacity_controller is not None:
        _conversion_capacity_controller.close()
        _conversion_capacity_controller = None


def get_quota_validator_service(
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> QuotaValidatorService:
    return QuotaValidatorService(access_control_service=access_control_service)


def get_canonical_layout_capture_service() -> CanonicalLayoutCaptureService:
    global _canonical_layout_capture_service
    if _canonical_layout_capture_service is None:
        _canonical_layout_capture_service = build_s3_canonical_layout_capture_service(
            enabled=read_bool_env("CANONICAL_LAYOUT_CAPTURE_ENABLED", default=False),
            bucket=os.getenv("CONVERSION_S3_BUCKET", ""),
            prefix=os.getenv(
                "CANONICAL_LAYOUT_CAPTURE_S3_PREFIX",
                "conversion/canonical-layouts/candidates/v1",
            ),
            region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            max_pages=int(os.getenv("CANONICAL_LAYOUT_CAPTURE_MAX_PAGES", "20")),
            max_extracted_chars=int(
                os.getenv("CANONICAL_LAYOUT_CAPTURE_MAX_EXTRACTED_CHARS", "250000")
            ),
        )
    return _canonical_layout_capture_service


def get_document_conversion_pipeline(
    processing_pipeline: ConversionPipeline = Depends(get_conversion_processing_pipeline),
    legacy_conversion_runner=Depends(get_legacy_conversion_runner),
    report_service: ReportService = Depends(get_report_service),
    document_preflight_service: DocumentPreflightService = Depends(get_document_preflight_service),
    quota_validator_service: QuotaValidatorService = Depends(get_quota_validator_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
    canonical_layout_capture_service: CanonicalLayoutCaptureService = Depends(
        get_canonical_layout_capture_service
    ),
) -> DocumentConversionPipeline:
    return DocumentConversionPipeline(
        report_service=report_service,
        access_control_service=access_control_service,
        document_preflight_service=document_preflight_service,
        quota_validator_service=quota_validator_service,
        processing_pipeline=processing_pipeline,
        analysis_repository=analysis_repository,
        canonical_layout_capture_service=canonical_layout_capture_service,
        legacy_conversion_runner=legacy_conversion_runner,
    )


def get_conversion_job_executor(
    document_conversion_pipeline: DocumentConversionPipeline = Depends(get_document_conversion_pipeline),
    document_store: ConversionDocumentStore = Depends(get_conversion_document_store),
    job_repository: ConversionJobRepository = Depends(get_conversion_job_repository),
) -> ConversionJobExecutor:
    return InlineConversionJobExecutor(
        document_conversion_pipeline=document_conversion_pipeline,
        document_store=document_store,
        job_repository=job_repository,
    )


def get_conversion_job_factory(
    access_control_service: AccessControlService = Depends(get_access_control_service),
    document_store: ConversionDocumentStore = Depends(get_conversion_document_store),
    job_repository: ConversionJobRepository = Depends(get_conversion_job_repository),
    cleanup_service: ConversionJobCleanupService = Depends(get_conversion_job_cleanup_service),
) -> ConversionJobFactory:
    return ConversionJobFactory(
        access_control_service=access_control_service,
        document_store=document_store,
        job_repository=job_repository,
        cleanup_service=cleanup_service,
    )


def get_convert_document_use_case(
    conversion_job_factory: ConversionJobFactory = Depends(get_conversion_job_factory),
    conversion_job_executor: ConversionJobExecutor = Depends(get_conversion_job_executor),
) -> ConvertDocumentUseCase:
    return ConvertDocumentUseCase(
        conversion_job_factory=conversion_job_factory,
        conversion_job_executor=conversion_job_executor
    )


def get_contact_service() -> ContactService:
    global _contact_service
    if _contact_service is None:
        _contact_service = ContactService.from_env()
    return _contact_service


def get_contact_form_service() -> ContactService | SmtpContactService:
    global _contact_form_service
    if _contact_form_service is None:
        provider = os.getenv("CONTACT_DELIVERY_PROVIDER", "resend").strip().lower()
        if provider == "resend":
            _contact_form_service = ContactService.from_env()
        elif provider in {"smtp", "hostinger_smtp"}:
            _contact_form_service = SmtpContactService.from_env()
        else:
            raise RuntimeError("CONTACT_DELIVERY_PROVIDER must be 'resend' or 'hostinger_smtp'.")
    return _contact_form_service


def get_google_oauth_service() -> GoogleOAuthService:
    global _google_oauth_service
    if _google_oauth_service is None:
        frontend_base_url = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").strip().rstrip("/")
        if not frontend_base_url:
            frontend_base_url = "http://localhost:3000"
        config = GoogleOAuthConfig(
            client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "").strip(),
            frontend_base_url=frontend_base_url,
            state_ttl_seconds=int(os.getenv("GOOGLE_OAUTH_STATE_TTL_SECONDS", "600")),
        )
        _google_oauth_service = GoogleOAuthService(
            config=config,
            access_control_service=get_access_control_service(),
        )
    return _google_oauth_service
