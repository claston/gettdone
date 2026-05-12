from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi.testclient import TestClient

from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    PdfProcessingMetrics,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes) -> AnalyzeResponse:
        if not filename.endswith((".csv", ".xlsx", ".ofx", ".pdf")):
            from app.application import UnsupportedFileTypeError

            raise UnsupportedFileTypeError

        return AnalyzeResponse(
            analysis_id="an_metrics123",
            file_type="pdf",
            transactions_total=2,
            total_inflows=100.0,
            total_outflows=-20.0,
            net_total=80.0,
            operational_summary=OperationalSummary(
                total_volume=120.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=2,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=0,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[CategorySummary(category="Outros", total=-20.0, count=1)],
            top_expenses=[
                TopExpense(
                    description="TEST",
                    amount=-20.0,
                    date="2026-04-01",
                    category="Outros",
                )
            ],
            insights=[
                Insight(
                    type="test",
                    title="Test insight",
                    description=f"Bytes: {len(raw_bytes)}",
                )
            ],
            preview_transactions=[
                TransactionPreview(
                    date="2026-04-01",
                    description="TEST",
                    amount=-20.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date="2026-04-01",
                    description_before="test",
                    description_after="TEST",
                    amount_before=-20.0,
                    amount_after=-20.0,
                )
            ],
            expires_at=None,
            pdf_processing_metrics=PdfProcessingMetrics(
                total_ms=20.0,
                parse_ms=10.0,
                classify_ms=2.0,
                normalize_ms=3.0,
                reconcile_ms=5.0,
                page_count=2,
                extracted_char_count=500,
                flattened_line_count=24,
                grouped_transactions_count=2,
                inline_candidates_count=2,
                inline_transactions_count=2,
                selected_parser="grouped",
                balance_consistency_checked=1,
                balance_consistency_failed=0,
                canonical_transactions_count=2,
                canonical_with_running_balance_count=2,
                canonical_with_external_reference_count=2,
                canonical_warning_count=1,
                canonical_balance_warning_count=1,
                canonical_warning_transactions_count=1,
                canonical_warning_types_count=1,
                canonical_warning_types="balance_consistency_failed",
                canonical_warning_types_list=["balance_consistency_failed"],
                canonical_running_balance_coverage_rate=1.0,
                canonical_external_reference_coverage_rate=1.0,
                canonical_warning_transaction_rate=0.5,
            ),
        )


class FakeReportService:
    def __init__(self) -> None:
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False)
        self._tmp.write(b"test-report")
        self._tmp.flush()
        self._path = Path(self._tmp.name)
        self._owners: dict[str, tuple[str, str]] = {}

    def get_report_path(self, analysis_id: str) -> Path:
        if analysis_id != "an_metrics123":
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        owner = self._owners.get(analysis_id)
        if owner is not None and owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        self._owners[analysis_id] = (identity_type, identity_id)

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        owner = self._owners.get(analysis_id)
        if owner is None:
            if allow_unowned:
                return
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError


class FakeAccessControlService:
    def resolve_identity(self, anonymous_fingerprint: str | None, user_token: str | None):
        class Identity:
            def __init__(self, identity_type: str, identity_id: str) -> None:
                self.identity_type = identity_type
                self.identity_id = identity_id

        token = (user_token or "").strip()
        fingerprint = (anonymous_fingerprint or "").strip()
        if token:
            return Identity("user", "usr_fake")
        if fingerprint:
            return Identity("anonymous", f"anon_{fingerprint}")
        from app.application import InvalidUserTokenError

        raise InvalidUserTokenError


def _build_client() -> TestClient:
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    return TestClient(app)


def test_analyze_happy_path_includes_pdf_processing_metrics_compat_fields() -> None:
    client = _build_client()

    response = client.post(
        "/analyze",
        data={"anonymous_fingerprint": "fp-metrics"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_id"] == "an_metrics123"
    assert payload["file_type"] == "pdf"
    assert payload["pdf_processing_metrics"]["selected_parser"] == "grouped"
    assert payload["pdf_processing_metrics"]["canonical_transactions_count"] == 2
    assert payload["pdf_processing_metrics"]["canonical_with_running_balance_count"] == 2
    assert payload["pdf_processing_metrics"]["canonical_with_external_reference_count"] == 2
    assert payload["pdf_processing_metrics"]["canonical_warning_types"] == "balance_consistency_failed"
    assert payload["pdf_processing_metrics"]["canonical_warning_types_list"] == [
        "balance_consistency_failed"
    ]
    app.dependency_overrides.clear()


def test_report_happy_path_for_analysis_created_by_analyze() -> None:
    client = _build_client()

    intake = client.post(
        "/analyze",
        data={"anonymous_fingerprint": "fp-owner"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )
    assert intake.status_code == 200

    report = client.get("/report/an_metrics123", params={"anonymous_fingerprint": "fp-owner"})
    assert report.status_code == 200
    app.dependency_overrides.clear()


def test_report_negative_path_returns_not_found_for_unknown_analysis_id() -> None:
    client = _build_client()

    response = client.get("/report/an_unknown", params={"anonymous_fingerprint": "fp-owner"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()
