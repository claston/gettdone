import shutil
import sqlite3
from pathlib import Path
from tempfile import mkdtemp

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app
from app.routers.auth import SESSION_ACCESS_COOKIE_NAME, SESSION_REFRESH_COOKIE_NAME


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


def _build_client(state_dir: Path) -> tuple[TestClient, AccessControlService]:
    access_control = _AccessControlServiceInMemory(
        state_file=state_dir / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    return TestClient(app), access_control


def test_session_login_sets_http_only_cookies_and_me_works_without_bearer() -> None:
    state_dir = Path(mkdtemp(prefix="auth-session-api-"))
    client, _service = _build_client(state_dir)
    try:
        register = client.post(
            "/auth/register",
            json={
                "name": "Erica",
                "email": "erica@example.com",
                "password": "strong-pass",
                "accepted_terms": True,
            },
        )
        assert register.status_code == 200

        response = client.post(
            "/auth/session/login",
            json={"email": "erica@example.com", "password": "strong-pass"},
        )
        assert response.status_code == 200
        assert response.cookies.get(SESSION_ACCESS_COOKIE_NAME)
        assert response.cookies.get(SESSION_REFRESH_COOKIE_NAME)

        me = client.get("/auth/me")
        assert me.status_code == 200
        payload = me.json()
        assert payload["email"] == "erica@example.com"
        assert payload["name"] == "Erica"
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_session_refresh_rotates_token_and_detects_reuse() -> None:
    state_dir = Path(mkdtemp(prefix="auth-session-api-"))
    client, _service = _build_client(state_dir)
    try:
        register = client.post(
            "/auth/register",
            json={
                "name": "Erica",
                "email": "erica@example.com",
                "password": "strong-pass",
                "accepted_terms": True,
            },
        )
        assert register.status_code == 200

        login = client.post(
            "/auth/session/login",
            json={"email": "erica@example.com", "password": "strong-pass"},
        )
        assert login.status_code == 200
        old_refresh = login.cookies.get(SESSION_REFRESH_COOKIE_NAME)
        assert old_refresh

        refreshed = client.post("/auth/session/refresh")
        assert refreshed.status_code == 200
        new_refresh = refreshed.cookies.get(SESSION_REFRESH_COOKIE_NAME)
        assert new_refresh
        assert new_refresh != old_refresh

        client.cookies.set(SESSION_REFRESH_COOKIE_NAME, old_refresh, path="/auth/session/refresh")
        reuse = client.post("/auth/session/refresh")
        assert reuse.status_code == 401
        assert "reuse" in str(reuse.json().get("detail", "")).lower()

        client.cookies.set(SESSION_REFRESH_COOKIE_NAME, new_refresh, path="/auth/session/refresh")
        broken_family = client.post("/auth/session/refresh")
        assert broken_family.status_code == 401
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)


def test_session_logout_revokes_cookie_session() -> None:
    state_dir = Path(mkdtemp(prefix="auth-session-api-"))
    client, _service = _build_client(state_dir)
    try:
        register = client.post(
            "/auth/register",
            json={
                "name": "Erica",
                "email": "erica@example.com",
                "password": "strong-pass",
                "accepted_terms": True,
            },
        )
        assert register.status_code == 200

        login = client.post(
            "/auth/session/login",
            json={"email": "erica@example.com", "password": "strong-pass"},
        )
        assert login.status_code == 200

        logout = client.post("/auth/session/logout")
        assert logout.status_code == 200
        assert logout.json()["status"] == "ok"

        me = client.get("/auth/me")
        assert me.status_code == 401
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(state_dir, ignore_errors=True)
