import os
from pathlib import Path

from app.application import AnalyzeService, ReportService, TempAnalysisStorage

_backend_root = Path(__file__).resolve().parents[1]
_storage = TempAnalysisStorage(
    root_dir=_backend_root / "tmp" / "analyses",
    ttl_seconds=int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")),
)
_analyze_service = AnalyzeService(storage=_storage)
_report_service = ReportService(storage=_storage)


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service
