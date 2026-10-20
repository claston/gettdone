from app.main import app
from tests.test_product_telemetry_api import _build_client

EVENTS = ("workspace_opened", "conversion_selected", "suspicious_filter_used", "transaction_edited",
          "conversion_reviewed", "batch_export_requested")
HEADERS = {"Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}


def test_workspace_events_require_registered_identity_and_discard_financial_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    client, service, _, _ = _build_client(tmp_path)
    try:
        assert client.post("/telemetry/events", headers=HEADERS, json={"event_type": EVENTS[0]}).status_code == 401
        client.post("/auth/session/login", json={"email": "buyer@example.com", "password": "strong-pass"})
        for event in EVENTS:
            assert client.post("/telemetry/events", headers=HEADERS, json={"event_type": event,
                "page_path": "/review/private-client?amount=100", "plan_code": "PRIVATE", "description": "PRIVATE",
                "amount": 100, "filename": "PRIVATE.pdf"}).status_code == 202
        with service._connect() as conn:
            rows = service._fetchall(conn, "SELECT event_type, page_path, plan_code, processing_id FROM product_events")
        assert {row["event_type"] for row in rows} == set(EVENTS)
        assert all(row["page_path"] == "/review" and row["plan_code"] is None and row["processing_id"] is None for row in rows)
        assert client.post("/telemetry/events", json={"event_type": EVENTS[0]}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_anonymous_workspace_telemetry_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    client, _, _, _ = _build_client(tmp_path)
    try:
        client.post("/auth/anonymous-session", json={})
        assert client.post("/telemetry/events", headers=HEADERS, json={"event_type": EVENTS[0]}).status_code == 401
    finally:
        app.dependency_overrides.clear()
