from pathlib import Path

from app.application import AnalyzeService, ReportService, TempAnalysisStorage

_storage = TempAnalysisStorage(root_dir=Path("tmp") / "analyses")
_analyze_service = AnalyzeService(storage=_storage)
_report_service = ReportService(storage=_storage)


def get_analyze_service() -> AnalyzeService:
    return _analyze_service


def get_report_service() -> ReportService:
    return _report_service

