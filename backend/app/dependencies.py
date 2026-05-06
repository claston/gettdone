import os
from pathlib import Path

from app.application import (
    AccessControlService,
    AnalyzeService,
    ContactService,
    GoogleOAuthConfig,
    GoogleOAuthService,
    ReportService,
    TempAnalysisStorage,
)
from app.security_baseline import is_production_env, read_bool_env

_backend_root = Path(__file__).resolve().parents[1]
_storage = TempAnalysisStorage(
    root_dir=_backend_root / "tmp" / "analyses",
    ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
)
_analyze_service = AnalyzeService(storage=_storage)
_report_service = ReportService(storage=_storage)
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


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service


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
        )
    return _access_control_service


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
