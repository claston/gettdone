from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.application.admin_overview import AdminOverviewService
from app.dependencies import get_access_control_service
from app.main import app

NOW = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)


@pytest.fixture
def course_data(tmp_path):
    clock = {"now": NOW}
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test",
                                   admin_emails={"admin@example.test"}, now_provider=lambda: clock["now"])
    users = {}
    for name in ("buyer", "visitor", "admin"):
        account = service.register_user(name=name, email=f"{name}@example.test", password="strong-test-password")
        users[name] = service.resolve_identity(anonymous_fingerprint=None, user_token=account.token)
    users["anonymous"] = service.resolve_identity(anonymous_fingerprint="private-browser", user_token=None)

    def record(name, event, source="conversao", seconds=0):
        clock["now"] = NOW + timedelta(seconds=seconds)
        path = "/ofx-convert.html" if event.startswith("course_cta_") else f"/curso-notas-explicativas.html?from={source}"
        service.record_product_event(identity=users[name], event_type=event, page_path=path)
        clock["now"] = NOW + timedelta(seconds=20)

    yield service, users, record
    service.close()


def test_course_funnel_uses_distinct_ordered_cohorts_and_separate_sources(course_data):
    service, users, record = course_data
    for name in ("buyer", "anonymous", "admin"):
        record(name, "course_cta_view")
    for _ in range(3):
        record("buyer", "course_cta_click", seconds=1)
        record("buyer", "course_page_view", seconds=2)
        record("buyer", "course_outbound_click", seconds=3)
    record("admin", "course_cta_click", seconds=1)
    record("admin", "course_page_view", seconds=2)
    # A click before exposure and an outbound before arrival must not inflate CTR.
    record("anonymous", "course_cta_click", seconds=-1)
    record("anonymous", "course_outbound_click", source="pagina", seconds=-1)
    record("anonymous", "course_page_view", source="pagina", seconds=1)
    record("visitor", "course_page_view", source="pagina")
    record("visitor", "course_outbound_click", source="email", seconds=1)
    record("visitor", "course_cta_view", seconds=-86400)
    overview = AdminOverviewService(service).get_overview(days=1)
    rows = {row["source"]: row for row in overview["course_funnel"]}
    conversion = rows["conversao"]
    assert (conversion["cta_view_people"], conversion["cta_click_people"]) == (2, 1)
    assert (conversion["page_view_people"], conversion["outbound_click_people"]) == (1, 1)
    assert (conversion["cta_ctr"], conversion["page_ctr"]) == (50.0, 100.0)
    assert rows["pagina"]["page_view_people"] == 2
    assert rows["pagina"]["outbound_click_people"] == 0
    assert rows["pagina"]["cta_ctr"] is None
    assert rows["email"]["page_ctr"] is None
    assert AdminDashboardService(service).get_dashboard(days=1)["course_funnel"] == overview["course_funnel"]
    anonymous = AdminOverviewService(service).get_overview(days=1, identity_type="anonymous")["course_funnel"]
    assert anonymous[0]["cta_view_people"] == 1 and anonymous[0]["cta_ctr"] == 0
    assert users["anonymous"].identity_id not in str(overview)


def test_course_badges_and_filters_include_visitors_without_conversions(course_data):
    service, users, record = course_data
    for name in ("buyer", "anonymous", "admin"):
        record(name, "course_page_view")
    record("buyer", "course_page_view", seconds=1)
    record("buyer", "course_outbound_click", seconds=2)
    app.dependency_overrides[get_access_control_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get("/admin/active-users?course_interest=visited").status_code == 401
            assert client.post("/admin/auth/login", json={"email": "admin@example.test",
                                                          "password": "strong-test-password"}).status_code == 200
            response = client.get("/admin/active-users?days=1&course_interest=visited&limit=1")
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 2
            payload = client.get("/admin/active-users?days=1&course_interest=outbound").json()
            assert payload["total"] == 1
            buyer = payload["items"][0]
            assert buyer["identity_reference"] == users["buyer"].identity_id
            assert buyer["conversions"] == 0
            assert buyer["course_page_views"] == 2 and buyer["course_outbound_clicks"] == 1
            assert buyer["last_course_page_view_at"] == (NOW + timedelta(seconds=1)).isoformat()
            assert buyer["last_course_outbound_click_at"] == (NOW + timedelta(seconds=2)).isoformat()
            assert buyer["marketing_contact_allowed"] is False
            assert client.get("/admin/active-users?course_interest=invalid").status_code == 400
            assert client.get("/admin/active-users?course_interest=visited&identity_type=registered").json()["total"] == 1
            assert client.get("/admin/active-users").json()["total"] == 0
    finally:
        app.dependency_overrides.clear()
