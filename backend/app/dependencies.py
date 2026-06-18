import os
from pathlib import Path

from fastapi import Depends

from app.application import (
    AccessControlService,
    AnalyzeDocumentRunner,
    ContactService,
    ConvertDocumentUseCase,
    DocumentConversionPipeline,
    DocumentPreflightService,
    GoogleOAuthConfig,
    GoogleOAuthService,
    QuotaValidatorService,
    ReportService,
    TempAnalysisStorage,
    build_default_conversion_pipeline,
    run_analysis,
)
from app.application.conversion_pipeline import ConversionPipeline
from app.application.repositories import AnalysisRepository
from app.security_baseline import is_production_env, read_bool_env

_backend_root = Path(__file__).resolve().parents[1]
_storage = TempAnalysisStorage(
    root_dir=_backend_root / "tmp" / "analyses",
    ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
)
_conversion_processing_pipeline = build_default_conversion_pipeline()
_report_service = ReportService(storage=_storage)
_document_preflight_service = DocumentPreflightService()
_access_control_service: AccessControlService | None = None
_contact_service: ContactService | None = None
_google_oauth_service: GoogleOAuthService | None = None


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


def get_analyze_document() -> AnalyzeDocumentRunner:
    def analyze_document(
        *,
        filename: str,
        raw_bytes: bytes,
        on_ocr_progress=None,
        max_ocr_pages: int | None = None,
        analysis_id: str | None = None,
    ):
        return run_analysis(
            storage=_storage,
            pipeline=_conversion_processing_pipeline,
            filename=filename,
            raw_bytes=raw_bytes,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            analysis_id=analysis_id,
        )

    return analyze_document


def get_legacy_conversion_runner():
    return None


def get_report_service() -> ReportService:
    return _report_service


def get_analysis_repository() -> AnalysisRepository:
    return _storage


def get_document_preflight_service() -> DocumentPreflightService:
    return _document_preflight_service


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
        )
    return _access_control_service


def close_access_control_service() -> None:
    global _access_control_service
    if _access_control_service is not None:
        _access_control_service.close()
        _access_control_service = None


def get_quota_validator_service(
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> QuotaValidatorService:
    return QuotaValidatorService(access_control_service=access_control_service)


def get_document_conversion_pipeline(
    processing_pipeline: ConversionPipeline = Depends(get_conversion_processing_pipeline),
    legacy_conversion_runner=Depends(get_legacy_conversion_runner),
    report_service: ReportService = Depends(get_report_service),
    document_preflight_service: DocumentPreflightService = Depends(get_document_preflight_service),
    quota_validator_service: QuotaValidatorService = Depends(get_quota_validator_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
) -> DocumentConversionPipeline:
    return DocumentConversionPipeline(
        report_service=report_service,
        access_control_service=access_control_service,
        document_preflight_service=document_preflight_service,
        quota_validator_service=quota_validator_service,
        processing_pipeline=processing_pipeline,
        analysis_repository=analysis_repository,
        legacy_conversion_runner=legacy_conversion_runner,
    )


def get_convert_document_use_case(
    document_conversion_pipeline: DocumentConversionPipeline = Depends(get_document_conversion_pipeline),
) -> ConvertDocumentUseCase:
    return ConvertDocumentUseCase(
        document_conversion_pipeline=document_conversion_pipeline
    )


def get_contact_service() -> ContactService:
    global _contact_service
    if _contact_service is None:
        _contact_service = ContactService.from_env()
    return _contact_service


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
