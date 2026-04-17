from pathlib import Path

from fastapi.testclient import TestClient

from app.application import AccessControlService, ReportService, TempAnalysisStorage
from app.application.models import AnalysisData, TransactionRow
from app.dependencies import get_access_control_service, get_report_service
from app.main import app


def _build_analysis_data(analysis_id: str = "an_convert123") -> AnalysisData:
    preview = [
        TransactionRow(
            date="2026-04-01",
            description="DEBIT A",
            amount=-20.0,
            category="Outros",
            reconciliation_status="unmatched",
        ),
        TransactionRow(
            date="2026-04-02",
            description="CREDIT B",
            amount=100.0,
            category="Outros",
            reconciliation_status="unmatched",
        ),
    ]
    return AnalysisData(
        analysis_id=analysis_id,
        file_type="pdf",
        upload_filename="extrato_nubank.pdf",
        transactions_total=2,
        total_inflows=100.0,
        total_outflows=-20.0,
        net_total=80.0,
        preview_transactions=preview,
        report_transactions=preview,
    )


def build_client(tmp_path: Path) -> TestClient:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    storage.save_analysis(_build_analysis_data())
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    owner = access_control.resolve_identity(anonymous_fingerprint="fp-owner", user_token=None)
    storage.set_convert_owner(
        analysis_id="an_convert123",
        identity_type=owner.identity_type,
        identity_id=owner.identity_id,
    )
    app.dependency_overrides[get_report_service] = lambda: ReportService(storage=storage)
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    return TestClient(app)


def test_convert_edits_updates_preview_and_csv_artifact(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-05",
                    "description": "EDITED CREDIT",
                    "credit": 45.75,
                    "debit": None,
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_id"] == "an_convert123"
    assert payload["preview_transactions"][0]["description"] == "EDITED CREDIT"
    assert payload["preview_transactions"][0]["amount"] == 45.75

    csv_report = client.get("/convert-report/an_convert123?format=csv&anonymous_fingerprint=fp-owner")
    assert csv_report.status_code == 200
    assert "2026-04-05,EDITED CREDIT,45.75" in csv_report.text
    assert isinstance(payload["updated_at"], str)
    app.dependency_overrides.clear()


def test_convert_edits_rejects_invalid_credit_debit_pair(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-05",
                    "description": "INVALID",
                    "credit": 10.0,
                    "debit": 10.0,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert "Provide only one of credit or debit" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_edits_deletes_row_and_updates_csv(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "action": "delete",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions_total"] == 1
    assert len(payload["preview_transactions"]) == 2
    assert payload["preview_transactions"][0]["description"] == "DEBIT A"
    assert payload["preview_transactions"][0]["is_deleted"] is True
    assert payload["preview_transactions"][1]["description"] == "CREDIT B"
    assert payload["preview_transactions"][1]["is_deleted"] is False

    csv_report = client.get("/convert-report/an_convert123?format=csv&anonymous_fingerprint=fp-owner")
    assert csv_report.status_code == 200
    assert "DEBIT A" not in csv_report.text
    assert "CREDIT B" in csv_report.text
    app.dependency_overrides.clear()


def test_convert_edits_inserts_row_and_updates_csv(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "action": "insert",
                    "insert_position": 2,
                    "date": "2026-04-03",
                    "description": "INSERTED C",
                    "credit": 30.0,
                    "debit": None,
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions_total"] == 3
    assert len(payload["preview_transactions"]) == 3
    assert payload["preview_transactions"][2]["description"] == "INSERTED C"
    assert payload["preview_transactions"][2]["amount"] == 30.0
    assert payload["preview_transactions"][2]["is_deleted"] is False

    csv_report = client.get("/convert-report/an_convert123?format=csv&anonymous_fingerprint=fp-owner")
    assert csv_report.status_code == 200
    assert "2026-04-03,INSERTED C,30.0" in csv_report.text
    app.dependency_overrides.clear()


def test_convert_edits_returns_not_found_for_missing_analysis(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_missing?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-05",
                    "description": "ANY",
                    "credit": 10.0,
                    "debit": None,
                }
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()


def test_convert_edits_rejects_access_from_other_identity(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-other",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-05",
                    "description": "ANY",
                    "credit": 10.0,
                    "debit": None,
                }
            ]
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied for this analysis."
    app.dependency_overrides.clear()


def test_convert_edits_returns_conflict_for_stale_version(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    first = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-05",
                    "description": "FIRST",
                    "credit": 10.0,
                    "debit": None,
                }
            ]
        },
    )
    assert first.status_code == 200
    stale_version = first.json()["updated_at"]

    second = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "expected_updated_at": stale_version,
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-06",
                    "description": "SECOND",
                    "credit": 15.0,
                    "debit": None,
                }
            ],
        },
    )
    assert second.status_code == 200

    conflict = client.post(
        "/convert-edits/an_convert123?anonymous_fingerprint=fp-owner",
        json={
            "expected_updated_at": stale_version,
            "edits": [
                {
                    "row_id": "row_1",
                    "date": "2026-04-07",
                    "description": "STALE",
                    "credit": 20.0,
                    "debit": None,
                }
            ],
        },
    )
    assert conflict.status_code == 409
    assert "changed since last load" in conflict.json()["detail"]
    app.dependency_overrides.clear()
