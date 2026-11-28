import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import posthog_analytics as analytics


@pytest.fixture
def collector(monkeypatch):
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    batches = []

    async def send(events):
        batches.extend(events)

    monkeypatch.setattr(analytics, "send_batch", send)
    app = FastAPI()
    app.add_middleware(analytics.AnalyticsMiddleware)

    @app.get("/event")
    def event():
        analytics.record_event("download", download_format="ofx", filename="SECRET.pdf", amount=100)
        return {"ok": True}

    client = TestClient(app)
    return client, batches


def consent(client, analytics_value=True):
    client.cookies.set("ofx_privacy_preferences_v1", quote(json.dumps({"analytics": analytics_value})))
    client.cookies.set("ofx_analytics_id", str(uuid4()))


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_explicit_boolean_consent_allows_export(collector, value):
    client, batches = collector
    consent(client, value)
    assert client.get("/event").status_code == 200
    assert batches == []


def test_opt_in_exports_only_allowlisted_metadata(collector):
    client, batches = collector
    consent(client)
    assert client.get("/event").status_code == 200
    assert len(batches) == 1
    assert batches[0]["properties"]["download_format"] == "ofx"
    assert "SECRET" not in json.dumps(batches)
    assert "amount" not in json.dumps(batches)
    assert batches[0]["properties"]["$geoip_disable"] is True


def test_revocation_and_disabled_export(collector, monkeypatch):
    client, batches = collector
    consent(client)
    monkeypatch.setenv("POSTHOG_ENABLED", "false")
    client.get("/event")
    assert not batches
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    consent(client, False)
    client.get("/event")
    assert not batches


def test_bad_identity_and_transport_failure_do_not_break_product(collector, monkeypatch):
    client, batches = collector
    consent(client)
    client.cookies.set("ofx_analytics_id", "person@example.com")
    assert client.get("/event").status_code == 200
    assert not batches
    consent(client)

    async def fail(events):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(analytics, "send_batch", fail)
    assert client.get("/event").json() == {"ok": True}


def test_request_context_is_cleared(collector):
    client, batches = collector
    consent(client)
    client.get("/event")
    analytics.record_event("download", download_format="csv")
    assert len(batches) == 1
    client.cookies.clear()
    client.get("/event")
    assert len(batches) == 1


def test_routes_remove_private_segments_and_queries():
    assert analytics.safe_page("/planos.html?email=secret") == "/planos.html"
    assert analytics.safe_page("/conversions/private-company") == "/conversions/:id"
    assert analytics.safe_page("/secret-company") == "/other"


def test_browser_endpoint_accepts_only_explicit_events(collector):
    from app.routers.telemetry import router

    client, batches = collector
    client.app.include_router(router)
    consent(client)
    response = client.post("/telemetry/analytics", json={"event_type": "preview_viewed", "page_path": "/review?secret=1"})
    assert response.status_code == 202
    assert batches[0]["properties"]["page_path"] == "/review"
    assert client.post("/telemetry/analytics", json={"event_type": "download"}).status_code == 400
    assert client.post("/telemetry/analytics", json={"event_type": "page_view", "filename": "secret"}).status_code == 422


def test_logout_removes_analytics_identity(collector):
    client, _ = collector

    @client.app.post("/auth/session/logout")
    def logout():
        return {}

    consent(client)
    response = client.post("/auth/session/logout")
    assert "ofx_analytics_id=;" in response.headers["set-cookie"]


def test_user_identification_uses_pseudonym_and_links_only_consented_visitor(collector):
    client, batches = collector

    @client.app.get("/registered")
    def registered():
        analytics.record_event("plans_view", user_id="internal-user-id")
        return {}

    consent(client)
    client.get("/registered")
    assert [event["event"] for event in batches] == ["$identify", "plans_view"]
    assert batches[0]["properties"]["$anon_distinct_id"] == client.cookies.get("ofx_analytics_id")
    assert "internal-user-id" not in json.dumps(batches)


def test_existing_product_event_is_exported_once_after_database_record(collector, tmp_path):
    from app.main import app
    from tests.test_product_telemetry_api import _build_client

    _, batches = collector
    client, _, _, _ = _build_client(tmp_path)
    try:
        consent(client)
        assert client.post("/auth/session/login", json={"email": "buyer@example.com", "password": "strong-pass"}).status_code == 200
        response = client.post("/telemetry/events", json={
            "event_type": "plan_cta_click", "plan_code": "profissional", "page_path": "/planos.html?email=secret",
        })
        assert response.status_code == 202
        assert [event["event"] for event in batches] == ["$identify", "plan_cta_click"]
        assert batches[1]["properties"]["plan_code"] == "profissional"
        assert "email" not in json.dumps(batches)
    finally:
        app.dependency_overrides.clear()


def test_transport_sends_posthog_batch_contract(monkeypatch):
    requests = []

    def accept(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "Ok"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(accept), **kwargs,
    ))
    events = [analytics._event("page_view", str(uuid4()), {"$geoip_disable": True})]
    asyncio.run(analytics.send_batch(events))
    assert str(requests[0].url) == "https://us.i.posthog.com/batch/"
    assert json.loads(requests[0].content)["batch"] == events


def test_worker_import_does_not_require_api_dependencies():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'fastapi', 'starlette', 'httpx'}:
        raise ImportError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app.posthog_analytics import record_event
record_event('download', download_format='ofx')
"""
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize("environment, expected", [("development", False), ("test", False), ("production", True), ("prod", True)])
def test_production_defaults_keep_test_data_out_of_project(monkeypatch, environment, expected):
    monkeypatch.delenv("POSTHOG_ENABLED", raising=False)
    monkeypatch.setenv("APP_ENV", environment)
    assert analytics.enabled() is expected
