from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.dependencies import get_access_control_service
from app.main import app


def _build_client(tmp_path: Path):
    clock = {"now": datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)}
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
        now_provider=lambda: clock["now"],
    )
    service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    buyer = service.register_user(
        name="Possível Cliente",
        email="buyer@example.com",
        password="strong-pass",
        product_updates_opt_in=True,
        product_updates_opted_in_at=clock["now"].isoformat(),
        product_updates_consent_version=1,
    )
    app.dependency_overrides[get_access_control_service] = lambda: service
    return TestClient(app), service, clock, buyer


def test_registered_product_events_feed_active_user_purchase_signals(tmp_path: Path) -> None:
    client, service, clock, buyer = _build_client(tmp_path)
    service.record_user_conversion(
        user_id=buyer.user_id,
        processing_id="an_buyer",
        filename="extrato.pdf",
        model="Nubank",
        conversion_type="pdf-conversion",
        status="Sucesso",
        transactions_count=30,
        pages_count=120,
        created_at=clock["now"].isoformat(),
    )

    try:
        login = client.post(
            "/auth/session/login",
            json={"email": "buyer@example.com", "password": "strong-pass"},
        )
        assert login.status_code == 200

        for event_type, plan_code in (
            ("plans_view", None),
            ("plan_cta_click", "profissional"),
            ("checkout_view", "profissional"),
        ):
            response = client.post(
                "/telemetry/events",
                json={"event_type": event_type, "page_path": "/planos.html", "plan_code": plan_code},
            )
            assert response.status_code == 202

        admin_login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert admin_login.status_code == 200
        response = client.get(
            "/admin/active-users",
            params={"days": 30, "identity_type": "registered", "prospect_only": True},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["registered_people"] == 1
        assert payload["summary"]["anonymous_people"] == 0
        assert payload["summary"]["pages_total"] == 120
        assert payload["summary"]["successes"] == 1
        assert payload["summary"]["failures"] == 0
        item = payload["items"][0]
        assert item["display_name"] == "Possível Cliente"
        assert item["email"] == "buyer@example.com"
        assert item["marketing_contact_allowed"] is True
        assert item["pages"] == 120
        assert item["top_models"] == [{"model": "Nubank", "count": 1}]
        assert item["plans_page_views"] == 1
        assert item["plan_cta_clicks"] == 1
        assert item["checkout_entries"] == 1
        assert item["purchase_profile"] == "quente"
        assert "alto volume de páginas" in item["purchase_reasons"]

        dashboard = client.get(
            "/admin/dashboard",
            params={"days": 30, "identity_type": "registered"},
        )
        assert dashboard.status_code == 200
        funnel = dashboard.json()["checkout_funnel"]
        assert funnel["plans_page_people_count"] == 1
        assert funnel["plan_cta_people_count"] == 1
        assert funnel["checkout_entry_people_count"] == 1
        interest = dashboard.json()["commercial_interest"][0]
        assert interest["display_name"] == "Possível Cliente"
        assert interest["email"] == "buyer@example.com"
        assert interest["plans_page_views"] == 1
        assert interest["plan_cta_clicks"] == 1
        assert interest["checkout_entries"] == 1
    finally:
        app.dependency_overrides.clear()


def test_anonymous_event_identity_is_taken_from_signed_cookie(tmp_path: Path) -> None:
    client, service, clock, _buyer = _build_client(tmp_path)
    try:
        session = client.post("/auth/anonymous-session", json={})
        assert session.status_code == 200
        event = client.post(
            "/telemetry/events",
            json={"event_type": "plans_view", "page_path": "/planos.html"},
        )
        assert event.status_code == 202

        with service._connect() as conn:
            row = service._fetchone(
                conn,
                "SELECT identity_type, identity_id, event_type FROM product_events",
            )
        assert row is not None
        assert row["identity_type"] == "anonymous"
        assert row["event_type"] == "plans_view"
        assert row["identity_id"]
    finally:
        app.dependency_overrides.clear()


def test_product_event_rejects_missing_identity_and_unknown_event(tmp_path: Path) -> None:
    client, _service, _clock, _buyer = _build_client(tmp_path)
    try:
        missing_identity = client.post(
            "/telemetry/events",
            json={"event_type": "plans_view", "page_path": "/planos.html"},
        )
        assert missing_identity.status_code == 401

        client.post("/auth/anonymous-session", json={})
        unknown = client.post(
            "/telemetry/events",
            json={"event_type": "arbitrary_event", "page_path": "/planos.html"},
        )
        assert unknown.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_dashboard_identifies_registered_plan_visitor_before_first_conversion(tmp_path: Path) -> None:
    _client, service, _clock, _buyer = _build_client(tmp_path)
    visitor = service.register_user(
        name="Visitante sem conversão",
        email="visitor@example.com",
        password="strong-pass",
    )
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=visitor.token)
    service.record_product_event(
        identity=identity,
        event_type="plans_view",
        page_path="/planos.html",
    )

    dashboard = AdminDashboardService(service).get_dashboard(days=30, identity_type="registered")

    assert dashboard["summary"]["active_people_count"] == 0
    assert dashboard["commercial_interest"][0]["display_name"] == "Visitante sem conversão"
    assert dashboard["commercial_interest"][0]["email"] == "visitor@example.com"
