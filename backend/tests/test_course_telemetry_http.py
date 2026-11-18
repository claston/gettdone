"""Affiliate funnel acceptance over a disposable local HTTP API."""

import pytest

from app.dependencies import get_access_control_service
from app.main import app
from tests.admin_setup import register_admin
from tests.ocr_conversion_http_helpers import public_client

pytestmark = pytest.mark.real_http


def test_course_tracking_and_existing_downloads_over_http(tmp_path):
    with public_client(tmp_path) as client:
        assert client.post("/telemetry/events", json={"event_type": "course_cta_view"}).status_code == 401
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        raw = b"date,description,amount\n2026-11-17,SYNTHETIC CREDIT,40.00\n2026-11-17,SYNTHETIC DEBIT,-10.00\n"
        response = client.post("/convert", files={"file": ("synthetic.csv", raw, "text/csv")})
        assert response.status_code == 200, response.text
        identifier = response.json()["analysis"]["analysis_id"]
        for event in ("course_cta_view", "course_cta_click", "course_page_view", "course_outbound_click"):
            response = client.post("/telemetry/events", json={
                "event_type": event, "page_path": "/curso-notas-explicativas.html?from=conversao",
            })
            assert response.status_code == 202, response.text
        assert client.get(f"/report/{identifier}").status_code == 200
        assert client.get(f"/convert-report/{identifier}?format=ofx").status_code == 200
        assert client.get("/report/nonexistent").status_code == 404
        assert client.post("/telemetry/events", json={"event_type": "course_purchase"}).status_code == 400
        # /analyze is retired; preserve its deployed contract and exercise /convert above.
        assert client.post("/analyze").status_code == 405
        assert client.get("/admin/dashboard/overview").status_code == 401
        service = app.dependency_overrides[get_access_control_service]()
        service.admin_emails.add("course-admin@example.test")
        register_admin(service, name="Admin", email="course-admin@example.test", password="strong-test-password")
        assert client.post("/admin/auth/login", json={"email": "course-admin@example.test",
                                                      "password": "strong-test-password"}).status_code == 200
        overview = client.get("/admin/dashboard/overview?days=1")
        assert overview.status_code == 200, overview.text
        funnel = overview.json()["course_funnel"][0]
        assert funnel["cta_ctr"] == 100 and funnel["page_ctr"] == 100
        visitors = client.get("/admin/active-users?days=1&course_interest=visited").json()
        assert visitors["total"] == 1
        assert visitors["items"][0]["course_page_views"] == 1
        assert visitors["items"][0]["course_outbound_clicks"] == 1
