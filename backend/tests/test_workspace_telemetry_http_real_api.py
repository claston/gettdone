import httpx
import pytest

from app.main import app
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_workspace_telemetry import EVENTS, HEADERS

pytestmark = pytest.mark.real_http


def test_workspace_events_over_real_authenticated_http(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", HEADERS["Origin"])
    services = []

    def configure(access):
        access.register_user(name="Sintético", email="workspace@example.invalid", password="safe-test-password")
        services.append(access)

    with _run_http_server(configure) as base, httpx.Client(base_url=base) as client:
        assert client.post("/telemetry/events", json={"event_type": EVENTS[0]}, headers=HEADERS).status_code == 401
        login = client.post("/auth/pro/session/login", headers=HEADERS,
                            json={"email": "workspace@example.invalid", "password": "safe-test-password"})
        assert login.status_code == 200, login.text
        for event in EVENTS:
            sent = client.post("/telemetry/events", headers=HEADERS, json={"event_type": event, "page_path": "/private"})
            assert sent.status_code == 202, sent.text
        assert client.post("/telemetry/events", headers=HEADERS, json={"event_type": "financial_description"}).status_code == 400
        assert client.post("/telemetry/events", json={"event_type": EVENTS[0]}).status_code == 403
        with services[0]._connect() as conn:
            rows = services[0]._fetchall(conn, "SELECT event_type, page_path FROM product_events")
        assert {row["event_type"] for row in rows} == set(EVENTS)
        assert all(row["page_path"] == "/review" for row in rows)
    assert app.dependency_overrides == {}
