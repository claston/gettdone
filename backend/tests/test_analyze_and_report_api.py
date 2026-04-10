from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi.testclient import TestClient

from app.dependencies import get_analyze_service, get_report_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    CategorySummary,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes) -> AnalyzeResponse:
        if not filename.endswith((".csv", ".xlsx", ".ofx")):
            from app.application import UnsupportedFileTypeError

            raise UnsupportedFileTypeError

        return AnalyzeResponse(
            analysis_id="an_test123",
            file_type="csv",
            transactions_total=1,
            total_inflows=100.0,
            total_outflows=-20.0,
            net_total=80.0,
            operational_summary=OperationalSummary(
                total_volume=120.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=1,
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
            expires_at=None,
        )


class FakeReportService:
    def __init__(self) -> None:
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False)
        self._tmp.write(b"test-report")
        self._tmp.flush()
        self._path = Path(self._tmp.name)

    def get_report_path(self, analysis_id: str) -> Path:
        if analysis_id != "an_test123":
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path


def build_client() -> TestClient:
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    return TestClient(app)


def test_analyze_happy_path() -> None:
    client = build_client()
    response = client.post(
        "/analyze",
        files={"file": ("sample.csv", b"date,description,amount\n2026-04-01,TEST,-20.0", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "an_test123"
    app.dependency_overrides.clear()


def test_analyze_unsupported_file_type() -> None:
    client = build_client()
    response = client.post(
        "/analyze",
        files={"file": ("sample.pdf", b"%PDF", "application/pdf")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_report_happy_path_and_not_found() -> None:
    client = build_client()

    ok = client.get("/report/an_test123")
    assert ok.status_code == 200

    missing = client.get("/report/an_unknown")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()
