import os
from pathlib import Path

from app.application import AccessControlService, AnalyzeService, ContactService, ReportService, TempAnalysisStorage

_backend_root = Path(__file__).resolve().parents[1]
_storage = TempAnalysisStorage(
    root_dir=_backend_root / "tmp" / "analyses",
    ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
)
_analyze_service = AnalyzeService(storage=_storage)
_report_service = ReportService(storage=_storage)
_access_control_service: AccessControlService | None = None
_contact_service: ContactService | None = None


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service


def get_access_control_service() -> AccessControlService:
    global _access_control_service
    if _access_control_service is None:
        token_secret = os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "dev-access-control-secret")
        default_anonymous_quota_limit = "9999" if token_secret == "dev-access-control-secret" else "3"
        _access_control_service = AccessControlService(
            state_file=_backend_root / "tmp" / "access_control" / "state.json",
            token_secret=token_secret,
            anonymous_quota_limit=int(os.getenv("ANONYMOUS_QUOTA_LIMIT", default_anonymous_quota_limit)),
        )
    return _access_control_service


def get_contact_service() -> ContactService:
    global _contact_service
    if _contact_service is None:
        _contact_service = ContactService.from_env()
    return _contact_service
