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


def test_convert_anonymous_quota_and_block_4th_attempt(tmp_path) -> None:
    client = build_client(tmp_path)

    for expected_remaining in [2, 1, 0]:
        response = client.post(
            "/convert",
            data={"anonymous_fingerprint": "anon-fp-a"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
        )
        assert response.status_code == 200
        assert response.json()["quota_remaining"] == expected_remaining

    blocked = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-a"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )
    assert blocked.status_code == 429
    assert "Quota exceeded" in blocked.json()["detail"]
    app.dependency_overrides.clear()


def test_register_then_convert_with_user_token(tmp_path) -> None:
    client = build_client(tmp_path)

    register = client.post(
        "/auth/register",
        json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
    )
    assert register.status_code == 200
    assert register.json()["quota_remaining"] == 10
    token = register.json()["user_token"]

    convert = client.post(
        "/convert",
        data={"user_token": token},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )
    assert convert.status_code == 200
    assert convert.json()["identity_type"] == "user"
    assert convert.json()["quota_remaining"] == 9
    app.dependency_overrides.clear()


def test_convert_rejects_file_bigger_than_2mb(tmp_path) -> None:
    client = build_client(tmp_path)
    oversized = b"a" * ((2 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-b"},
        files={"file": ("sample.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413
    assert "maximum size of 2 MB" in response.json()["detail"]
    app.dependency_overrides.clear()
