from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


def test_plans_returns_public_versioned_catalog(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.get("/plans")
        assert response.status_code == 200
        payload = response.json()
        assert "items" in payload
        assert len(payload["items"]) >= 3
        codes = {item["code"] for item in payload["items"]}
        assert {"essencial", "profissional", "escritorio"}.issubset(codes)
        assert all(int(item["version"]) >= 1 for item in payload["items"])
    finally:
        app.dependency_overrides.clear()


def test_admin_activate_plan_updates_user_subscription(tmp_path, monkeypatch) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    created = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    intent = service.create_checkout_intent(
        user_id=created.user_id,
        plan_code="profissional",
        customer_name="Erica",
        customer_email="erica@example.com",
        customer_whatsapp="+55 11 99999-0000",
    )
    monkeypatch.setenv("PLANS_ADMIN_TOKEN", "pricing-admin-secret")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/plans/activate",
            headers={"x-admin-token": "pricing-admin-secret"},
            json={"user_id": created.user_id, "plan_code": "profissional"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["user_id"] == created.user_id
        assert payload["plan_code"] == "profissional"
        assert payload["quota_mode"] == "pages"
        assert payload["quota_limit"] == 300

        identity = service.resolve_identity(anonymous_fingerprint=None, user_token=created.token)
        assert identity.plan_code == "profissional"
        assert identity.quota_limit == 300
        checkout_intent = service.read_checkout_intent_for_user(intent_id=str(intent["id"]), user_id=created.user_id)
        assert checkout_intent is not None
        assert checkout_intent["status"] == "RELEASED_FOR_USE"
    finally:
        app.dependency_overrides.clear()


def test_admin_activate_plan_requires_valid_admin_token(tmp_path, monkeypatch) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    created = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    service.create_checkout_intent(
        user_id=created.user_id,
        plan_code="profissional",
        customer_name="Erica",
        customer_email="erica@example.com",
        customer_whatsapp="+55 11 99999-0000",
    )
    monkeypatch.setenv("PLANS_ADMIN_TOKEN", "pricing-admin-secret")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/plans/activate",
            headers={"x-admin-token": "wrong-token"},
            json={"user_id": created.user_id, "plan_code": "essencial"},
        )
        assert response.status_code == 401
        assert "Invalid admin token" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_admin_activate_plan_accepts_bearer_token_and_legacy_path(tmp_path, monkeypatch) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    created = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    service.create_checkout_intent(
        user_id=created.user_id,
        plan_code="profissional",
        customer_name="Erica",
        customer_email="erica@example.com",
        customer_whatsapp="+55 11 99999-0000",
    )
    monkeypatch.setenv("PLANS_ADMIN_TOKEN", "pricing-admin-secret")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/plans/activate",
            headers={"authorization": "Bearer pricing-admin-secret"},
            json={"user_id": created.user_id, "plan_code": "essencial"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["plan_code"] == "essencial"
        assert payload["quota_mode"] == "pages"
        assert payload["quota_limit"] == 150
    finally:
        app.dependency_overrides.clear()


def test_admin_activate_plan_requires_configuration(tmp_path, monkeypatch) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    created = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    monkeypatch.delenv("PLANS_ADMIN_TOKEN", raising=False)
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/plans/activate",
            headers={"x-admin-token": "any-value"},
            json={"user_id": created.user_id, "plan_code": "essencial"},
        )
        assert response.status_code == 401
        assert "Invalid admin token" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_admin_activate_plan_accepts_admin_user_token_when_legacy_token_not_set(tmp_path, monkeypatch) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    admin_user = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    created = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    monkeypatch.delenv("PLANS_ADMIN_TOKEN", raising=False)
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/plans/activate",
            headers={"authorization": f"Bearer {admin_user.token}"},
            json={"user_id": created.user_id, "plan_code": "essencial"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["plan_code"] == "essencial"
    finally:
        app.dependency_overrides.clear()
