from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application import GoogleOAuthConfig, GoogleOAuthService
from app.application.access_control import AccessControlService
from app.application.errors import InvalidCredentialsError, InvalidSessionTokenError, InvalidUserTokenError
from app.dependencies import get_access_control_service, get_google_oauth_service
from app.routers.auth import router


@pytest.mark.parametrize("verification_required", [False, True])
def test_google_link_invalidates_preexisting_password_tokens_and_sessions(tmp_path, verification_required):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test-secret",
                                   email_verification_required=verification_required)
    try:
        pending = service.register_user("Unverified signup", "owner@example.test", "old-password")
        session = None if verification_required else service.create_user_session(user_id=pending.user_id)
        linked = service.register_or_authenticate_google_user(
            provider_user_id="verified-google-subject", email=pending.email, name="Verified Owner")
        assert linked.user_id == pending.user_id
        with pytest.raises(InvalidCredentialsError):
            service.authenticate_user(pending.email, "old-password")
        with pytest.raises(InvalidUserTokenError):
            service.get_user_by_token(pending.token)
        if session:
            with pytest.raises(InvalidSessionTokenError):
                service.get_user_by_session_access_token(session.access_token)
        assert service.get_user_by_token(linked.token).user_id == pending.user_id
    finally:
        service.close()


def test_public_local_registration_cannot_bootstrap_admin_even_after_restart(tmp_path):
    settings = dict(state_file=tmp_path / "state.json", token_secret="test-secret", admin_emails={"admin@example.test"})
    service = AccessControlService(**settings)
    user = service.register_user("Someone", "admin@example.test", "chosen-password")
    try:
        assert not user.is_admin
    finally:
        service.close()
    service = AccessControlService(**settings)
    try:
        assert not service.is_user_admin(user_id=user.user_id)
        service.set_user_admin_role(user_id=user.user_id, is_admin=True)
    finally:
        service.close()
    service = AccessControlService(**settings)
    try:
        assert service.is_user_admin(user_id=user.user_id)
    finally:
        service.close()


def test_startup_retires_password_credentials_from_previously_linked_google_accounts(tmp_path):
    settings = dict(state_file=tmp_path / "state.json", token_secret="test-secret")
    service = AccessControlService(**settings)
    user = service.register_user("Legacy link", "linked@example.test", "old-password")
    session = service.create_user_session(user_id=user.user_id)
    with service._connect() as conn:
        service._execute(conn, "UPDATE users SET auth_provider = 'google', provider_user_id = 'legacy-sub' WHERE id = ?",
                         (user.user_id,))
        conn.commit()
    service.close()
    service = AccessControlService(**settings)
    try:
        with pytest.raises(InvalidUserTokenError):
            service.get_user_by_token(user.token)
        with pytest.raises(InvalidSessionTokenError):
            service.get_user_by_session_access_token(session.access_token)
        linked = service.register_or_authenticate_google_user(
            provider_user_id="legacy-sub", email=user.email, name=user.name)
        service.close()
        service = AccessControlService(**settings)
        assert service.get_user_by_token(linked.token).user_id == user.user_id
    finally:
        service.close()


def test_google_callback_requires_the_browser_that_started_it(tmp_path, monkeypatch):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test-secret")
    oauth = GoogleOAuthService(config=GoogleOAuthConfig(
        client_id="test-client", client_secret="test-secret", redirect_uri="http://testserver/auth/google/callback",
        frontend_base_url="http://testserver"), access_control_service=service)
    exchanges = []
    monkeypatch.setattr(oauth, "_exchange_code_for_token", lambda **kw: exchanges.append(kw) or {"access_token": "mock"})
    monkeypatch.setattr(oauth, "_fetch_google_profile", lambda **kw: {
        "sub": "test-google", "email": "owner@example.test", "name": "Owner", "email_verified": True})
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_access_control_service] = lambda: service
    application.dependency_overrides[get_google_oauth_service] = lambda: oauth
    try:
        with TestClient(application) as original, TestClient(application) as unrelated:
            started = original.get("/auth/google/start?flow=signup&accepted_terms=true", follow_redirects=False)
            state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            assert "httponly" in started.headers.get("set-cookie", "").lower()
            assert started.headers["cache-control"] == "no-store"
            url = f"/auth/google/callback?code=test-code&state={state}"
            rejected = unrelated.get(url, follow_redirects=False)
            assert "error=google_oauth_failed" in rejected.headers["location"]
            assert not exchanges
            assert unrelated.get("/auth/me").status_code == 401
            accepted = original.get(url, follow_redirects=False)
            assert "error=" not in accepted.headers["location"]
            assert original.get("/auth/me").status_code == 200
            assert len(exchanges) == 1
            original.get(url, follow_redirects=False)
            assert len(exchanges) == 1
    finally:
        service.close()
