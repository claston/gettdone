import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_access_control_service
from app.main import app
from tests.http_runtime import loopback_request, serve_http

SYNTHETIC_NUMBER = "55" + "11" + "9" + "0" * 8


@pytest.mark.parametrize("configured", [None, "", "   ", "123", "0" * 13, "1" * 16, "javascript:alert(1)", "１２３４５６７８９"])
def test_public_config_disables_whatsapp_without_valid_international_number(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("WHATSAPP_SUPPORT_NUMBER", raising=False)
    else:
        monkeypatch.setenv("WHATSAPP_SUPPORT_NUMBER", configured)
    response = TestClient(app).get("/public-config")
    assert response.status_code == 200
    assert response.json() == {"whatsapp_support_number": None}


def test_public_config_exposes_only_allowed_config_without_database_or_login(monkeypatch):
    monkeypatch.setenv("WHATSAPP_SUPPORT_NUMBER", f" {SYNTHETIC_NUMBER} ")
    monkeypatch.setenv("CONTACT_SMTP_PASSWORD", "must-not-be-public")

    def unavailable_database():
        raise AssertionError("Public contact configuration must not query the database")

    app.dependency_overrides[get_access_control_service] = unavailable_database
    try:
        response = TestClient(app).get("/public-config")
        assert response.status_code == 200
        assert response.json() == {"whatsapp_support_number": SYNTHETIC_NUMBER}
        assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.pop(get_access_control_service, None)


@pytest.mark.real_http
def test_public_config_http_reflects_parameter_and_removal(monkeypatch):
    with serve_http(app) as base_url:
        monkeypatch.setenv("WHATSAPP_SUPPORT_NUMBER", SYNTHETIC_NUMBER)
        response = loopback_request("GET", f"{base_url}/public-config", timeout=5)
        assert response.status_code == 200
        assert response.json() == {"whatsapp_support_number": SYNTHETIC_NUMBER}
        monkeypatch.delenv("WHATSAPP_SUPPORT_NUMBER")
        response = loopback_request("GET", f"{base_url}/public-config", timeout=5)
        assert response.status_code == 200
        assert response.json() == {"whatsapp_support_number": None}
