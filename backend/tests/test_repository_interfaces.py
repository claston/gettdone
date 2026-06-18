from pathlib import Path
from uuid import uuid4

from app.application.analysis_response_builder import build_analyze_response, persist_conversion_result
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.models import AnalysisData
from app.application.report_service import ReportService


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.saved_analysis: AnalysisData | None = None

    def save_analysis(self, data: AnalysisData) -> str:
        self.saved_analysis = data
        return "2026-06-13T20:00:00+00:00"


class FakeReportRepository:
    def __init__(self) -> None:
        self.owner_calls: list[tuple[str, str, str]] = []
        self.requested_formats: list[tuple[str, str]] = []

    def get_report_path(self, analysis_id: str) -> Path:
        return Path(f"/tmp/{analysis_id}/report.xlsx")

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.owner_calls.append((analysis_id, identity_type, identity_id))

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        _ = (analysis_id, identity_type, identity_id, allow_unowned)

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
        _ = (closing_balance, bank_branch, account_number, bank_code)
        self.requested_formats.append((analysis_id, file_format))
        return Path(f"/tmp/{analysis_id}/converted.{file_format}")

    def get_upload_filename(self, analysis_id: str) -> str | None:
        return f"{analysis_id}.pdf"

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.owner_calls.append((analysis_id, identity_type, identity_id))

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        _ = (analysis_id, identity_type, identity_id)

    def list_convert_history(self, identity_type: str, identity_id: str, limit: int = 20) -> list[dict[str, str]]:
        _ = (identity_type, identity_id, limit)
        return []

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
        _ = (
            analysis_id,
            edits,
            expected_updated_at,
            opening_balance,
            closing_balance,
            bank_branch,
            account_number,
            bank_code,
        )
        return {}

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]:
        _ = (summary, reconciliation_rows, problems)
        return ("rec_123", "2026-06-13T20:00:00+00:00")

    def get_reconcile_report_path(self, analysis_id: str, file_format: str) -> Path:
        return Path(f"/tmp/{analysis_id}/reconcile.{file_format}")

    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.owner_calls.append((analysis_id, identity_type, identity_id))

    def assert_reconcile_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        _ = (analysis_id, identity_type, identity_id, allow_unowned)


def test_analyze_service_accepts_analysis_repository_protocol() -> None:
    repository = FakeAnalysisRepository()

    pipeline_result = build_default_conversion_pipeline().run(
        filename="sample.csv",
        raw_bytes=b"date,description,amount\n2026-04-01,IFOOD,-58.90\n",
        analysis_id=f"an_{uuid4().hex[:12]}",
    )
    persisted_result = persist_conversion_result(storage=repository, pipeline_result=pipeline_result)
    result = build_analyze_response(persisted_result=persisted_result)

    assert result.analysis_id.startswith("an_")
    assert result.expires_at == "2026-06-13T20:00:00+00:00"
    assert repository.saved_analysis is not None
    assert repository.saved_analysis.file_type == "csv"
    assert repository.saved_analysis.transactions_total == 1


def test_report_service_accepts_report_repository_protocol() -> None:
    repository = FakeReportRepository()
    service = ReportService(storage=repository)

    service.set_convert_owner("an_123", "anonymous", "fp_123")
    report_path = service.get_convert_report_path("an_123", "csv")

    assert repository.owner_calls == [("an_123", "anonymous", "fp_123")]
    assert repository.requested_formats == [("an_123", "csv")]
    assert report_path.name == "converted.csv"
