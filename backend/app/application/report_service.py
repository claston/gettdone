from pathlib import Path

from app.application.storage_service import TempAnalysisStorage


class ReportService:
    def __init__(self, storage: TempAnalysisStorage) -> None:
        self.storage = storage

    def get_report_path(self, analysis_id: str) -> Path:
        return self.storage.get_report_path(analysis_id)

