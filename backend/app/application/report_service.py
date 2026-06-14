from pathlib import Path

from app.application.repositories import ReportRepository


class ReportService:
    def __init__(self, storage: ReportRepository) -> None:
        self.storage = storage

    def get_report_path(self, analysis_id: str) -> Path:
        return self.storage.get_report_path(analysis_id)

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.storage.set_report_owner(analysis_id=analysis_id, identity_type=identity_type, identity_id=identity_id)

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self.storage.assert_report_owner(
            analysis_id=analysis_id,
            identity_type=identity_type,
            identity_id=identity_id,
            allow_unowned=allow_unowned,
        )

    def get_convert_report_path(
        self,
        analysis_id: str,
        file_format: str,
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Path:
        return self.storage.get_convert_report_path(
            analysis_id,
            file_format=file_format,
            closing_balance=closing_balance,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=bank_code,
        )

    def get_upload_filename(self, analysis_id: str) -> str | None:
        return self.storage.get_upload_filename(analysis_id)

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.storage.set_convert_owner(analysis_id=analysis_id, identity_type=identity_type, identity_id=identity_id)

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.storage.assert_convert_owner(analysis_id=analysis_id, identity_type=identity_type, identity_id=identity_id)

    def list_convert_history(self, identity_type: str, identity_id: str, limit: int = 20) -> list[dict[str, str]]:
        return self.storage.list_convert_history(
            identity_type=identity_type,
            identity_id=identity_id,
            limit=limit,
        )

    def apply_convert_edits(
        self,
        analysis_id: str,
        edits: list[dict[str, object]],
        expected_updated_at: str | None = None,
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> dict[str, object]:
        return self.storage.apply_convert_edits(
            analysis_id=analysis_id,
            edits=edits,
            expected_updated_at=expected_updated_at,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=bank_code,
        )

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

    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.storage.set_reconcile_owner(
            analysis_id=analysis_id,
            identity_type=identity_type,
            identity_id=identity_id,
        )

    def assert_reconcile_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self.storage.assert_reconcile_owner(
            analysis_id=analysis_id,
            identity_type=identity_type,
            identity_id=identity_id,
            allow_unowned=allow_unowned,
        )
