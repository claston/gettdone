import os
from pathlib import Path

from app.application import AccessControlService, AnalyzeService, ReportService, TempAnalysisStorage

_backend_root = Path(__file__).resolve().parents[1]
_storage = TempAnalysisStorage(
    root_dir=_backend_root / "tmp" / "analyses",
    ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
)
_analyze_service = AnalyzeService(storage=_storage)
_report_service = ReportService(storage=_storage)
_token_secret = os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "dev-access-control-secret")
_default_anonymous_quota_limit = "9999" if _token_secret == "dev-access-control-secret" else "3"
_access_control_service = AccessControlService(
    state_file=_backend_root / "tmp" / "access_control" / "state.json",
    token_secret=_token_secret,
    anonymous_quota_limit=int(os.getenv("ANONYMOUS_QUOTA_LIMIT", _default_anonymous_quota_limit)),
)


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service


def get_access_control_service() -> AccessControlService:
    return _access_control_service
