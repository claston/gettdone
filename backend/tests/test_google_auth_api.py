from fastapi.testclient import TestClient

from app.application import GoogleOAuthConfig, GoogleOAuthService, GoogleOAuthStateError
from app.application.access_control import AccessControlService
from app.dependencies import get_google_oauth_service
from app.main import app


class _FakeConfig:
    frontend_base_url = "http://localhost:3000"


class FakeGoogleOAuthService:
    def __init__(self) -> None:
        self.config = _FakeConfig()

    def build_authorization_url(
        self,
        *,
        next_path: str,
        flow_mode: str = "login",
        terms_accepted: bool = False,
        product_updates_opt_in: bool = False,
    ) -> str:
        _ = (flow_mode, terms_accepted, product_updates_opt_in)
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


class StubGoogleOAuthService(GoogleOAuthService):
    def __init__(self, *, access_control_service: AccessControlService, profile: dict) -> None:
        super().__init__(
            config=GoogleOAuthConfig(
                client_id="test-client",
                client_secret="test-secret",
                redirect_uri="http://testserver/auth/google/callback",
                frontend_base_url="http://localhost:3000",
            ),
            access_control_service=access_control_service,
        )
        self._profile = profile

    def _exchange_code_for_token(self, *, code: str, code_verifier: str) -> dict:
        _ = (code, code_verifier)
        return {"access_token": "google-access-token"}

    def _fetch_google_profile(self, *, access_token: str) -> dict:
        _ = access_token
        return self._profile


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
    assert "error_detail=GoogleOAuthStateError" in location

    app.dependency_overrides.clear()


def test_google_auth_start_requires_terms_for_signup_flow() -> None:
    app.dependency_overrides[get_google_oauth_service] = lambda: FakeGoogleOAuthService()
    client = TestClient(app)

    response = client.get("/auth/google/start?next=%2Fsignup.html&flow=signup", follow_redirects=False)
    assert response.status_code == 400
    assert "Termos de Uso" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_google_signup_records_initial_access_and_login_records_return(tmp_path) -> None:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    profile = {
        "sub": "google-user-123",
        "email": "erica@example.com",
        "name": "Erica",
        "email_verified": True,
    }
    oauth = StubGoogleOAuthService(access_control_service=access_control, profile=profile)

    signup_state, _ = access_control.create_google_oauth_state(
        next_path="/client-area.html",
        flow_mode="signup",
        terms_accepted=True,
    )
    signup_redirect = oauth.build_callback_redirect_url(code="signup-code", state=signup_state)
    assert "auth-callback.html" in signup_redirect
    user = access_control.get_user_by_email("erica@example.com")
    signup_events = access_control.list_user_login_events_for_admin(user_id=user.user_id)
    assert len(signup_events) == 1
    assert signup_events[0]["auth_method"] == "google_oauth"

    login_state, _ = access_control.create_google_oauth_state(
        next_path="/client-area.html",
        flow_mode="login",
    )
    login_redirect = oauth.build_callback_redirect_url(code="login-code", state=login_state)
    assert "auth-callback.html" in login_redirect
    events = access_control.list_user_login_events_for_admin(user_id=user.user_id)
    assert len(events) == 2
    assert events[0]["auth_method"] == "google_oauth"
