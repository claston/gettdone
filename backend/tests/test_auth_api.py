import shutil
import sqlite3
from pathlib import Path
from tempfile import mkdtemp

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


class _InMemoryConnCtx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _AccessControlServiceInMemory(AccessControlService):
    def __init__(self, **kwargs) -> None:
        self._test_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._test_conn.row_factory = sqlite3.Row
        super().__init__(**kwargs)

    def _connect(self) -> _InMemoryConnCtx:
        return _InMemoryConnCtx(self._test_conn)


def build_client(state_dir: Path) -> tuple[TestClient, AccessControlService]:
    access_control = _AccessControlServiceInMemory(
        state_file=state_dir / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    return TestClient(app), access_control


def test_login_returns_user_token_and_registered_quota() -> None:
    state_dir = Path(mkdtemp(prefix="auth-api-"))
    client, _service = build_client(state_dir)

    try:
        register = client.post(
            "/auth/register",
            json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
        )
        assert register.status_code == 200

        response = client.post(
            "/auth/login",
            json={"email": "erica@example.com", "password": "strong-pass"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["email"] == "erica@example.com"
        assert payload["name"] == "Erica"
        assert payload["user_token"]
        assert payload["quota_remaining"] == 10
        assert payload["quota_limit"] == 10
        assert payload["quota_mode"] == "conversion"
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_login_rejects_invalid_credentials() -> None:
    state_dir = Path(mkdtemp(prefix="auth-api-"))
    client, _service = build_client(state_dir)

    try:
        client.post(
            "/auth/register",
            json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
        )

        response = client.post(
            "/auth/login",
            json={"email": "erica@example.com", "password": "wrong-pass"},
        )

        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_auth_me_returns_user_profile_for_valid_token() -> None:
    state_dir = Path(mkdtemp(prefix="auth-api-"))
    client, _service = build_client(state_dir)

    try:
        register = client.post(
            "/auth/register",
            json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
        )
        token = register.json()["user_token"]

        response = client.get("/auth/me", params={"user_token": token})

        assert response.status_code == 200
        payload = response.json()
        assert payload["email"] == "erica@example.com"
        assert payload["name"] == "Erica"
        assert payload["quota_remaining"] == 10
        assert payload["quota_limit"] == 10
        assert payload["quota_mode"] == "conversion"
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_auth_me_rejects_invalid_token() -> None:
    state_dir = Path(mkdtemp(prefix="auth-api-"))
    client, _service = build_client(state_dir)

    try:
        response = client.get("/auth/me", params={"user_token": "invalid"})

        assert response.status_code == 401
        assert "Invalid user token" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_auth_me_reflects_active_pages_plan(tmp_path: Path) -> None:
    client, service = build_client(tmp_path)

    try:
        register = client.post(
            "/auth/register",
            json={"name": "Erica", "email": "erica@example.com", "password": "strong-pass"},
        )
        user_id = register.json()["user_id"]
        token = register.json()["user_token"]

        service.activate_user_plan(user_id=user_id, plan_code="essencial")

        response = client.get("/auth/me", params={"user_token": token})
        assert response.status_code == 200
        payload = response.json()
        assert payload["quota_mode"] == "pages"
        assert payload["quota_limit"] == 150
        assert payload["plan_code"] == "essencial"
    finally:
        app.dependency_overrides.clear()
