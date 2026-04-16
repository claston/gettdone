from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service, get_analyze_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
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
            analysis_id="an_convert123",
            file_type="pdf",
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
            insights=[Insight(type="test", title="Test insight", description=f"Bytes: {len(raw_bytes)}")],
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
        )


def build_client(tmp_path) -> TestClient:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    return TestClient(app)


def test_convert_happy_path(tmp_path) -> None:
    client = build_client(tmp_path)
    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-a"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_id"] == "an_convert123"
    assert payload["identity_type"] == "anonymous"
    assert payload["quota_remaining"] == 2
    assert payload["quota_limit"] == 3
    assert payload["analysis"]["analysis_id"] == "an_convert123"
    app.dependency_overrides.clear()


def test_convert_rejects_unsupported_file_type(tmp_path) -> None:
    client = build_client(tmp_path)
    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-b"},
        files={"file": ("sample.txt", b"unsupported", "text/plain")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_rejects_file_larger_than_2mb(tmp_path) -> None:
    client = build_client(tmp_path)
    oversized = b"a" * ((2 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-c"},
        files={"file": ("sample.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 413
    assert "maximum size of 2 MB" in response.json()["detail"]
    app.dependency_overrides.clear()
