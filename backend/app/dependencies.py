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


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service


def get_access_control_service() -> AccessControlService:
    global _access_control_service
    if _access_control_service is None:
        token_secret = os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "dev-access-control-secret")
        anonymous_quota_limit = int(os.getenv("ANONYMOUS_QUOTA_LIMIT", "3"))
        unlimited_anon_quota = os.getenv("UNLIMITED_ANON_QUOTA", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if unlimited_anon_quota:
            anonymous_quota_limit = 9999
        _access_control_service = AccessControlService(
            state_file=_backend_root / "tmp" / "access_control" / "state.json",
            token_secret=token_secret,
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
