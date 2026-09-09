from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.dependencies import get_access_control_service
from app.main import app


def _record_user_conversion(
    service: AccessControlService,
    *,
    user_id: str,
    processing_id: str,
    created_at: datetime,
    status: str = "Sucesso",
    transactions_count: int = 10,
    duration_ms: int = 1000,
    warning_count: int = 0,
    balance_failed: int = 0,
    error_code: str | None = None,
    error_stage: str | None = None,
) -> None:
    service.record_user_conversion(
        user_id=user_id,
        processing_id=processing_id,
        filename=f"{processing_id}.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status=status,
        transactions_count=transactions_count,
        duration_ms=duration_ms,
        canonical_warning_transactions_count=warning_count,
        balance_consistency_failed=balance_failed,
        error_code=error_code,
        error_stage=error_stage,
        created_at=created_at.isoformat(),
    )


def _record_anonymous_conversion(
    service: AccessControlService,
    clock: dict[str, datetime],
    *,
    fingerprint: str,
    event_id: str,
    created_at: datetime,
    status: str = "Sucesso",
    transactions_count: int = 10,
    duration_ms: int = 1000,
    warning_count: int = 0,
    balance_failed: int = 0,
    error_code: str | None = None,
    error_stage: str | None = None,
) -> None:
    clock["now"] = created_at
    service.record_anonymous_conversion_event(
        event_id=event_id,
        anonymous_fingerprint=fingerprint,
        filename=f"{event_id}.pdf",
        model="Itaú",
        conversion_type="pdf-ofx",
        status=status,
        transactions_count=transactions_count,
        pages_count=2,
        scanned_likely=False,
        ocr_used=False,
        ocr_pages_processed=0,
        duration_ms=duration_ms,
        canonical_warning_transactions_count=warning_count,
        balance_consistency_failed=balance_failed,
        error_code=error_code,
        error_stage=error_stage,
    )


def _build_dashboard_client(tmp_path: Path) -> tuple[TestClient, AccessControlService, dict[str, datetime], str]:
    clock = {"now": datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)}
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
        now_provider=lambda: clock["now"],
    )
    service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    return TestClient(app), service, clock, user.user_id


def test_admin_dashboard_aggregates_quality_failures_and_returning_people(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)

    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_before_period",
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_user_clean",
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        duration_ms=1000,
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_user_review",
        created_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        duration_ms=3000,
        warning_count=2,
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-returning",
        event_id="ace_failed",
        created_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        duration_ms=5000,
        error_code="parse_failed",
        error_stage="extraction",
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-returning",
        event_id="ace_clean_return",
        created_at=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        duration_ms=7000,
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-new",
        event_id="ace_clean_new",
        created_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        duration_ms=9000,
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get("/admin/dashboard", params={"days": 30, "identity_type": "all"})

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        assert payload["days"] == 30
        assert payload["timezone"] == "America/Sao_Paulo"
        assert payload["summary"] == {
            "conversions_total": 5,
            "technical_success_count": 4,
            "technical_success_rate": 80.0,
            "clean_conversion_count": 3,
            "clean_conversion_rate": 60.0,
            "failure_count": 1,
            "active_people_count": 3,
            "returning_people_count": 2,
            "median_duration_ms": 5000,
        }
        assert payload["identities"] == {
            "registered_conversions": 2,
            "registered_people": 1,
            "anonymous_conversions": 3,
            "anonymous_people": 2,
        }
        assert len(payload["daily"]) == 30
        september_fourth = next(item for item in payload["daily"] if item["date"] == "2026-09-04")
        assert september_fourth == {
            "date": "2026-09-04",
            "conversions": 1,
            "clean": 0,
            "review": 1,
            "failures": 0,
        }
        assert payload["top_errors"] == [
            {"error_code": "parse_failed", "error_stage": "extraction", "count": 1}
        ]
        assert [item["processing_id"] for item in payload["recent_attention"]] == [
            "an_user_review",
            "ace_failed",
        ]
        assert "anonymous_fingerprint" not in str(payload)
        assert "filename" not in str(payload)
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_filters_registered_conversions(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_registered",
        created_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-filtered",
        event_id="ace_anonymous",
        created_at=datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc),
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200
        response = client.get(
            "/admin/dashboard",
            params={"days": 7, "identity_type": "registered"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["conversions_total"] == 1
        assert payload["identities"]["registered_conversions"] == 1
        assert payload["identities"]["anonymous_conversions"] == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_requires_admin_access(tmp_path: Path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.get("/admin/dashboard")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_groups_days_in_sao_paulo_timezone(tmp_path: Path) -> None:
    now = datetime(2026, 9, 9, 2, 45, tzinfo=timezone.utc)
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        now_provider=lambda: now,
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    _record_user_conversion(
        service,
        user_id=user.user_id,
        processing_id="an_timezone",
        created_at=datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc),
    )

    payload = AdminDashboardService(service).get_dashboard(days=1)

    assert payload["start_at"] == "2026-09-08T03:00:00+00:00"
    assert payload["daily"] == [
        {
            "date": "2026-09-08",
            "conversions": 1,
            "clean": 1,
            "review": 0,
            "failures": 0,
        }
    ]


def test_admin_dashboard_is_physically_independent_from_worker_runtime() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_paths = (
        repository_root / "backend" / "app" / "routers" / "admin_auth.py",
        repository_root
        / "backend"
        / "app"
        / "application"
        / "admin_dashboard_service.py",
    )

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert "app.workers" not in source
        assert "conversion_jobs" not in source
        assert "conversion_batches" not in source
        assert "sqs" not in source.lower()
        assert "conversion_lambda" not in source.lower()
        assert "boto3" not in source.lower()
