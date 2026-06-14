from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.application.models import AnalysisData


class AnalysisRepository(Protocol):
    def save_analysis(self, data: AnalysisData) -> str: ...


class ReportRepository(Protocol):
    def get_report_path(self, analysis_id: str) -> Path: ...

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None: ...

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None: ...

    def get_convert_report_path(
        self,
        analysis_id: str,
        file_format: str,
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Path: ...

    def get_upload_filename(self, analysis_id: str) -> str | None: ...

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None: ...

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None: ...

    def list_convert_history(self, identity_type: str, identity_id: str, limit: int = 20) -> list[dict[str, str]]: ...

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
    ) -> dict[str, object]: ...

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]: ...

    def get_reconcile_report_path(self, analysis_id: str, file_format: str) -> Path: ...

    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None: ...

    def assert_reconcile_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None: ...
