from pathlib import Path

from fastapi.testclient import TestClient

from app.application import ReportService, TempAnalysisStorage
from app.application.models import AnalysisData, TransactionRow
from app.dependencies import get_report_service
from app.main import app


def _build_analysis_data(analysis_id: str = "an_convert123") -> AnalysisData:
    return AnalysisData(
        analysis_id=analysis_id,
        file_type="pdf",
        upload_filename="extrato_nubank.pdf",
        transactions_total=1,
        total_inflows=100.0,
        total_outflows=-20.0,
        net_total=80.0,
        preview_transactions=[
            TransactionRow(
                date="2026-04-01",
                description="TEST",
                amount=-20.0,
                category="Outros",
                reconciliation_status="unmatched",
            )
        ],
        report_transactions=[
            TransactionRow(
                date="2026-04-01",
                description="TEST",
                amount=-20.0,
                category="Outros",
                reconciliation_status="unmatched",
            )
        ],
    )


def build_client(tmp_path: Path) -> TestClient:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    storage.save_analysis(_build_analysis_data())
    app.dependency_overrides[get_report_service] = lambda: ReportService(storage=storage)
    return TestClient(app)


def test_convert_report_download_happy_path(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.get("/convert-report/an_convert123?format=ofx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ofx")
    assert "extrato_nubank_convertido.ofx" in response.headers["content-disposition"]
    assert "<STMTTRN>" in response.text
    app.dependency_overrides.clear()


def test_convert_report_download_csv_happy_path(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.get("/convert-report/an_convert123?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "extrato_nubank_convertido.csv" in response.headers["content-disposition"]
    assert "date,description,amount" in response.text
    app.dependency_overrides.clear()


def test_convert_report_returns_not_found_for_missing_analysis(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.get("/convert-report/an_missing?format=ofx")

    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()
