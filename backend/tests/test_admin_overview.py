"""Summary parity and bounded reads on SQLite and disposable PostgreSQL."""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.application.admin_overview import AdminOverviewService
from tests.test_admin_dashboard_api import _record_anonymous_conversion, _record_user_conversion

NOW = datetime(2026, 9, 9, 15, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgres"])
def sample(request, tmp_path):
    dsn = os.getenv("ADMIN_METRICS_TEST_DATABASE_URL") if request.param == "postgres" else None
    if request.param == "postgres" and not dsn:
        pytest.skip("Requires disposable local PostgreSQL")
    schema = f"ofx69_test_{uuid4().hex}"
    if dsn:
        from tests.postgres_runtime import migrate_test_schema
        assert urlparse(dsn).hostname in {"localhost", "127.0.0.1"}
        migrate_test_schema(dsn, schema)
    clock = {"now": NOW}
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="overview-test",
        database_url=dsn, database_schema=schema, now_provider=lambda: clock["now"])
    try:
        user = service.register_user(name="Pessoa", email="person@example.test", password="strong-password")
        identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
        anonymous = service.resolve_identity(anonymous_fingerprint="visitor", user_token=None)
        for i, age in enumerate([50, 8, 6, 1, 0, 0, 0, 0]):
            date = NOW-timedelta(days=age, hours=1)
            options = {"status": "Falha"} if i == 4 else {"warning_count": 1} if i == 5 else {}
            if i == 6:
                options = {"status": "Falha", "error_code": "quota_exceeded"}
            if i == 7:
                options = {"transactions_count": 0}
            _record_user_conversion(service, user_id=user.user_id, processing_id=f"an_{i}", created_at=date,
                                    duration_ms=101+i, ocr_used=i % 2 == 0, **options)
            _record_anonymous_conversion(service, clock, fingerprint="visitor", event_id=f"anon_{i}", created_at=date,
                                         duration_ms=202+i, **options)
        with service._connect() as conn:
            for table in ("user_conversions", "anonymous_conversion_events"):
                service._execute(conn, f"UPDATE {table} SET quality_status=NULL WHERE transactions_count=0 OR duration_ms IN (101,202)")
            conn.commit()
        clock["now"] = NOW
        for index, actor in enumerate((identity, anonymous)):
            clock["now"] = NOW-timedelta(seconds=2-index)
            for event in ("plans_view", "plans_view", "plan_cta_click", "checkout_view", "download", "client_area_view"):
                service.record_product_event(identity=actor, event_type=event, page_path="/planos.html",
                                             plan_code="profissional", download_format="ofx" if event == "download" else None)
        clock["now"] = NOW
        order = service.create_checkout_intent(user_id=user.user_id, plan_code="profissional",
            customer_name="Pessoa", customer_email="person@example.test", customer_whatsapp="+55 11 99999-2222")
        service.mark_checkout_intent_released_by_id(intent_id=order["id"])
        yield service
    finally:
        service.close()
        if dsn:
            import psycopg
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize("identity_type", ["all", "registered", "anonymous"])
@pytest.mark.parametrize("days", [1, 7, 30])
def test_overview_matches_existing_totals_and_commercial_interest(sample, identity_type, days):
    full = AdminDashboardService(sample).get_dashboard(days=days, identity_type=identity_type)
    overview = AdminOverviewService(sample).get_overview(days=days, identity_type=identity_type)
    assert set(overview) == {"days", "start_at", "end_at", "timezone", "summary", "checkout_funnel", "commercial_interest"}
    for key in overview:
        assert overview[key] == full[key], key


def test_overview_does_not_load_detailed_history(sample, monkeypatch):
    with sample._connect() as conn:
        for index in range(500):
            sample._execute(conn, """INSERT INTO product_events
                (id,event_type,identity_type,identity_id,created_at,plan_code)
                VALUES (?, 'plans_view', 'anonymous', ?, ?, 'essencial')""",
                (f"bulk-{index}", f"private-browser-{index % 50}", NOW.isoformat()))
        conn.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError("Detailed history must stay on demand")
    monkeypatch.setattr(AdminDashboardService, "get_dashboard", forbidden)
    monkeypatch.setattr(AdminDashboardService, "_load_period_events", forbidden)
    fetched = []
    fetchall = sample._fetchall
    def capture(conn, sql, params=()):
        rows = fetchall(conn, sql, params)
        fetched.extend(rows)
        assert "failure_diagnostics_json" not in sql and "result_payload" not in sql
        return rows
    monkeypatch.setattr(sample, "_fetchall", capture)
    result = AdminOverviewService(sample).get_overview()
    assert result["summary"]["conversions_total"] > 0
    assert result["checkout_funnel"]["plans_page_views_count"] == 504
    assert len(result["commercial_interest"]) == 20
    assert len(fetched) < 60
    assert "private-browser" not in str(result)


def test_legacy_quality_uses_the_same_policy(sample):
    cases = [("explicit_no_activity", "generic", 0.1, "low", "pdf-ofx"),
             ("layout_confirmed_no_activity", "specific", 0.9, None, "pdf-ofx"),
             ("financial_report", "investment_statement_events_v1", 0.99, "high", "pdf-ofx"),
             ("financial_report", "investment_statement_events_v1", 0.94, "high", "pdf-ofx"),
             ("inline", "specific", 0.99, "high", "csv-ofx")]
    for parser, layout, confidence, band, conversion_type in cases:
        with sample._connect() as conn:
            sample._execute(conn, """UPDATE user_conversions SET quality_status=NULL, selected_parser=?,
                layout_inference_name=?, layout_inference_confidence=?, parser_confidence_band=?, conversion_type=?
                WHERE analysis_id='an_7'""", (parser, layout, confidence, band, conversion_type))
            conn.commit()
        assert AdminOverviewService(sample).get_overview()["summary"] == AdminDashboardService(sample).get_dashboard()["summary"]


def test_empty_period_returns_real_zero_totals(sample):
    sample.now_provider = lambda: NOW+timedelta(days=100)
    overview = AdminOverviewService(sample).get_overview()
    full = AdminDashboardService(sample).get_dashboard()
    assert overview["summary"] == full["summary"]
    assert overview["checkout_funnel"] == full["checkout_funnel"]
    assert overview["commercial_interest"] == []


def test_overview_rejects_authenticated_non_admin(sample):
    from fastapi.testclient import TestClient

    from app.dependencies import get_access_control_service
    from app.main import app

    user = sample.register_user(name="Regular", email="regular@example.test", password="strong-password")
    session = sample.create_user_session(user_id=user.user_id, ip_address=None, user_agent=None)
    app.dependency_overrides[get_access_control_service] = lambda: sample
    try:
        response = TestClient(app).get("/admin/dashboard/overview", headers={"Authorization": f"Bearer {session.access_token}"})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
