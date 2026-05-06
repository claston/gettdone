import shutil
import sqlite3
from pathlib import Path
from tempfile import mkdtemp

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
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


class _InMemoryConnCtx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _AccessControlServiceInMemory(AccessControlService):
    def __init__(self, **kwargs) -> None:
        self._test_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._test_conn.row_factory = sqlite3.Row
        super().__init__(**kwargs)

    def _connect(self) -> _InMemoryConnCtx:
        return _InMemoryConnCtx(self._test_conn)


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
            pdf_processing_metrics=PdfProcessingMetrics(
                total_ms=1.0,
                parse_ms=1.0,
                classify_ms=0.0,
                normalize_ms=0.0,
                reconcile_ms=0.0,
                page_count=3,
                extracted_char_count=10,
                flattened_line_count=1,
                grouped_transactions_count=1,
                inline_candidates_count=0,
                inline_transactions_count=0,
                selected_parser="test",
            ),
        )


class FakeReportService:
    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        _ = (analysis_id, identity_type, identity_id)


def build_client(state_dir: Path) -> tuple[TestClient, AccessControlService]:
    access_control = _AccessControlServiceInMemory(
        state_file=state_dir / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    return TestClient(app), access_control


def test_convert_anonymous_quota_and_block_4th_attempt() -> None:
    state_dir = Path(mkdtemp(prefix="convert-auth-api-"))
    client, _service = build_client(state_dir)

    try:
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
        detail = blocked.json()["detail"]
        assert detail["code"] == "weekly_quota_exceeded"
        assert detail["identity_type"] == "anonymous"
        assert detail["quota_limit"] == 3
        assert detail["quota_remaining"] == 0
        assert isinstance(detail["reset_at"], str)
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_register_then_convert_with_user_token() -> None:
    state_dir = Path(mkdtemp(prefix="convert-auth-api-"))
    client, _service = build_client(state_dir)

    try:
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
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_convert_rejects_file_bigger_than_2mb() -> None:
    state_dir = Path(mkdtemp(prefix="convert-auth-api-"))
    client, _service = build_client(state_dir)
    oversized = b"a" * ((2 * 1024 * 1024) + 1)

    try:
        response = client.post(
            "/convert",
            data={"anonymous_fingerprint": "anon-fp-b"},
            files={"file": ("sample.pdf", oversized, "application/pdf")},
        )
        assert response.status_code == 413
        assert "maximum size of 2 MB" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_paid_pages_plan_consumes_quota_by_page_count() -> None:
    state_dir = Path(mkdtemp(prefix="convert-auth-api-"))
    client, service = build_client(state_dir)

    try:
        register = client.post(
            "/auth/register",
            json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
        )
        user_id = register.json()["user_id"]
        token = register.json()["user_token"]
        service.activate_user_plan(user_id=user_id, plan_code="essencial")

        convert = client.post(
            "/convert",
            data={"user_token": token},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
        )
        assert convert.status_code == 200
        payload = convert.json()
        assert payload["quota_limit"] == 150
        assert payload["quota_remaining"] == 147
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)
