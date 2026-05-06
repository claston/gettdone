from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


def test_admin_login_returns_admin_token_for_admin_user(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["role"] == "admin"
        assert payload["admin_token"]
    finally:
        app.dependency_overrides.clear()


def test_admin_login_blocks_non_admin_user(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post(
            "/admin/auth/login",
            json={"email": "erica@example.com", "password": "strong-pass"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_me_accepts_bearer_token_for_admin_user(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.get("/admin/me", headers={"authorization": f"Bearer {admin.token}"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["email"] == "admin@example.com"
        assert payload["role"] == "admin"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_list_users_and_filter_admins(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.get(
            "/admin/users",
            params={"query": "example.com", "only_admin": "true", "limit": 10, "offset": 0},
            headers={"authorization": f"Bearer {admin.token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 1
        assert payload["items"]
        assert all(item["is_admin"] for item in payload["items"])
    finally:
        app.dependency_overrides.clear()


def test_admin_can_grant_and_revoke_admin_role_for_other_user(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        promote = client.post(
            "/admin/users/role",
            headers={"authorization": f"Bearer {admin.token}"},
            json={"user_id": user.user_id, "is_admin": True},
        )
        assert promote.status_code == 200
        assert promote.json()["is_admin"] is True

        revoke = client.post(
            "/admin/users/role",
            headers={"authorization": f"Bearer {admin.token}"},
            json={"user_id": user.user_id, "is_admin": False},
        )
        assert revoke.status_code == 200
        assert revoke.json()["is_admin"] is False
    finally:
        app.dependency_overrides.clear()


def test_admin_user_role_history_tracks_actor_and_transitions(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        promote = client.post(
            "/admin/users/role",
            headers={"authorization": f"Bearer {admin.token}"},
            json={"user_id": user.user_id, "is_admin": True},
        )
        assert promote.status_code == 200

        revoke = client.post(
            "/admin/users/role",
            headers={"authorization": f"Bearer {admin.token}"},
            json={"user_id": user.user_id, "is_admin": False},
        )
        assert revoke.status_code == 200

        history = client.get(
            f"/admin/users/{user.user_id}/history",
            headers={"authorization": f"Bearer {admin.token}"},
        )
        assert history.status_code == 200
        payload = history.json()
        assert payload["user_id"] == user.user_id
        assert len(payload["items"]) >= 2
        event_types = {item["event_type"] for item in payload["items"]}
        assert "ADMIN_ROLE_GRANTED" in event_types
        assert "ADMIN_ROLE_REVOKED" in event_types
        assert any(item["actor_email"] == "admin@example.com" for item in payload["items"])
    finally:
        app.dependency_overrides.clear()
