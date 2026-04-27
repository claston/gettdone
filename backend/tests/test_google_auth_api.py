from fastapi.testclient import TestClient

from app.application import GoogleOAuthStateError
from app.dependencies import get_google_oauth_service
from app.main import app


class _FakeConfig:
    frontend_base_url = "http://localhost:3000"


class FakeGoogleOAuthService:
    def __init__(self) -> None:
        self.config = _FakeConfig()

    def build_authorization_url(self, *, next_path: str) -> str:
        return f"https://accounts.google.com/mock?next={next_path}"

    def build_callback_redirect_url(self, *, code: str, state: str) -> str:
        return (
            "http://localhost:3000/auth-callback.html"
            f"?user_token=test-token-{code}-{state}&next=%2Fclient-area.html&provider=google"
        )


class FakeGoogleOAuthServiceWithError(FakeGoogleOAuthService):
    def build_callback_redirect_url(self, *, code: str, state: str) -> str:
        _ = (code, state)
        raise GoogleOAuthStateError


def test_google_auth_start_redirects_to_google() -> None:
    app.dependency_overrides[get_google_oauth_service] = lambda: FakeGoogleOAuthService()
    client = TestClient(app)

    response = client.get("/auth/google/start?next=%2Fofx-convert.html", follow_redirects=False)
    assert response.status_code == 307
    assert "accounts.google.com/mock" in response.headers["location"]
    assert "next=/ofx-convert.html" in response.headers["location"]

    app.dependency_overrides.clear()


def test_google_auth_callback_redirects_to_frontend_callback() -> None:
    app.dependency_overrides[get_google_oauth_service] = lambda: FakeGoogleOAuthService()
    client = TestClient(app)

    response = client.get("/auth/google/callback?code=abc123&state=state123", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://localhost:3000/auth-callback.html")
    assert "user_token=test-token-abc123-state123" in location
    assert "next=%2Fclient-area.html" in location

    app.dependency_overrides.clear()


def test_google_auth_callback_redirects_with_error_when_state_invalid() -> None:
    app.dependency_overrides[get_google_oauth_service] = lambda: FakeGoogleOAuthServiceWithError()
    client = TestClient(app)

    response = client.get("/auth/google/callback?code=abc123&state=expired", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://localhost:3000/auth-callback.html")
    assert "error=google_oauth_failed" in location

    app.dependency_overrides.clear()
