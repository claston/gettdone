from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.application import GoogleOAuthCallbackResult, GoogleOAuthConfig, GoogleOAuthService, GoogleOAuthStateError
from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service, get_google_oauth_service
from app.main import app
from app.routers.auth import SESSION_ACCESS_COOKIE_NAME, SESSION_REFRESH_COOKIE_NAME


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

    def complete_callback(self, *, code: str, state: str) -> GoogleOAuthCallbackResult:
        return GoogleOAuthCallbackResult(
            redirect_url=(
                "http://localhost:3000/auth-callback.html"
                f"?next=%2Fclient-area.html&provider=google&proof={code}-{state}"
            )
        )


class FakeGoogleOAuthServiceWithError(FakeGoogleOAuthService):
    def complete_callback(self, *, code: str, state: str) -> GoogleOAuthCallbackResult:
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
    assert "user_token=" not in location
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


def test_google_signup_authorization_marks_current_marketing_consent_version(tmp_path) -> None:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    oauth = StubGoogleOAuthService(
        access_control_service=access_control,
        profile={
            "sub": "google-marketing-v2",
            "email": "marketing-v2@example.com",
            "name": "Marketing V2",
            "email_verified": True,
        },
    )

    authorization_url = oauth.build_authorization_url(
        next_path="/client-area.html",
        flow_mode="signup",
        terms_accepted=True,
        product_updates_opt_in=True,
    )
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    result = oauth.complete_callback(code="signup-code", state=state)
    assert result.user is not None
    with access_control._connect() as conn:
        stored = access_control._fetchone(
            conn,
            "SELECT product_updates_opt_in, product_updates_consent_version FROM users WHERE id = ?",
            (result.user.user_id,),
        )

    assert stored is not None
    assert bool(stored["product_updates_opt_in"]) is True
    assert stored["product_updates_consent_version"] == 2


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
    assert "user_token=" not in signup_redirect
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
    assert "user_token=" not in login_redirect
    events = access_control.list_user_login_events_for_admin(user_id=user.user_id)
    assert len(events) == 2
    assert events[0]["auth_method"] == "google_oauth"


def test_google_callback_sets_http_only_session_without_url_credential(tmp_path) -> None:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    oauth = StubGoogleOAuthService(
        access_control_service=access_control,
        profile={
            "sub": "google-cookie-user",
            "email": "cookie@example.com",
            "name": "Cookie User",
            "email_verified": True,
        },
    )
    state, _ = access_control.create_google_oauth_state(
        next_path="/client-area.html",
        flow_mode="signup",
        terms_accepted=True,
    )
    app.dependency_overrides[get_google_oauth_service] = lambda: oauth
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    client = TestClient(app)
    try:
        response = client.get(
            f"/auth/google/callback?code=google-code&state={state}",
            follow_redirects=False,
        )

        assert response.status_code == 307
        assert "user_token=" not in response.headers["location"]
        assert response.cookies.get(SESSION_ACCESS_COOKIE_NAME)
        assert response.cookies.get(SESSION_REFRESH_COOKIE_NAME)
        assert "httponly" in response.headers.get("set-cookie", "").lower()

        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "cookie@example.com"
    finally:
        app.dependency_overrides.clear()
