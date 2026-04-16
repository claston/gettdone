from pathlib import Path

from app.application.storage_service import TempAnalysisStorage


class ReportService:
    def __init__(self, storage: TempAnalysisStorage) -> None:
        self.storage = storage

    def get_report_path(self, analysis_id: str) -> Path:
        return self.storage.get_report_path(analysis_id)

    def get_convert_report_path(self, analysis_id: str, file_format: str) -> Path:
        return self.storage.get_convert_report_path(analysis_id, file_format=file_format)

    def get_upload_filename(self, analysis_id: str) -> str | None:
        return self.storage.get_upload_filename(analysis_id)

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]:
        return self.storage.save_reconcile_report(
            summary=summary,
            reconciliation_rows=reconciliation_rows,
            problems=problems,
        )

    def get_reconcile_report_path(self, analysis_id: str, file_format: str) -> Path:
        return self.storage.get_reconcile_report_path(analysis_id=analysis_id, file_format=file_format)
