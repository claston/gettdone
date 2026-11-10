from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.application import AccessControlService, ContactDeliveryError
from app.dependencies import get_access_control_service, get_contact_service
from app.main import app
from tests.test_email_verification_api import _FakeEmailService


@pytest.fixture
def recovery(tmp_path):
    now = [datetime(2026, 11, 9, 12, tzinfo=timezone.utc)]
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test", now_provider=lambda: now[0])
    mail = _FakeEmailService()
    app.dependency_overrides[get_access_control_service] = lambda: service
    app.dependency_overrides[get_contact_service] = lambda: mail
    user = service.register_user(name="Érica", email="erica@example.com", password="old-password")
    try:
        yield TestClient(app), service, mail, user, now
    finally:
        app.dependency_overrides.clear()
        service.close()


def request_token(client, mail):
    response = client.post("/auth/forgot-password", json={"email": " ERICA@example.com "})
    assert response.status_code == 202
    return mail.messages[-1]["text"].split("#token=", 1)[1].splitlines()[0]


def test_reset_changes_password_once_and_revokes_all_old_credentials(recovery):
    client, service, mail, user, _now = recovery
    login = client.post("/auth/session/login", json={"email": user.email, "password": "old-password"})
    assert login.status_code == 200
    old_cookies = dict(client.cookies)
    token = request_token(client, mail)
    with service._connect() as conn:
        row = conn.execute("SELECT token_hash FROM password_reset_tokens").fetchone()
        assert row["token_hash"] != token
    reset = client.post("/auth/reset-password", json={"token": token, "password": "new-password"})
    assert reset.status_code == 200
    client.cookies.clear()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {user.token}"}).status_code == 401
    client.cookies.update(old_cookies)
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/session/refresh").status_code == 401
    assert client.post("/auth/reset-password", json={"token": token, "password": "another-password"}).status_code == 400
    assert client.post("/auth/login", json={"email": user.email, "password": "old-password"}).status_code == 401
    login = client.post("/auth/login", json={"email": user.email, "password": "new-password"})
    assert login.status_code == 200
    client.cookies.clear()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['user_token']}"}).status_code == 200


def test_request_is_generic_and_rate_limited_per_account(recovery):
    client, _service, mail, _user, _now = recovery
    known = client.post("/auth/forgot-password", json={"email": "erica@example.com"})
    unknown = client.post("/auth/forgot-password", json={"email": "unknown@example.com"})
    repeated = client.post("/auth/forgot-password", json={"email": "erica@example.com"})
    assert known.status_code == unknown.status_code == repeated.status_code == 202
    assert known.json() == unknown.json() == repeated.json()
    assert len(mail.messages) == 1
    assert known.headers["cache-control"] == "no-store"


def test_expiry_resend_and_invalid_tokens(recovery):
    client, _service, mail, _user, now = recovery
    first = request_token(client, mail)
    now[0] += timedelta(seconds=61)
    second = request_token(client, mail)
    assert first != second
    for token in (first, "invalid-token", second + "tampered"):
        assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 400
    now[0] += timedelta(minutes=30)
    assert client.post("/auth/reset-password", json={"token": second, "password": "new-password"}).status_code == 400


@pytest.mark.parametrize("password", ["short", "x" * 257])
def test_invalid_password_does_not_consume_link(recovery, password):
    client, _service, mail, _user, _now = recovery
    token = request_token(client, mail)
    invalid = client.post("/auth/reset-password", json={"token": token, "password": password})
    assert invalid.status_code == 400
    assert token not in invalid.text
    assert password not in invalid.text
    assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 200


@pytest.mark.parametrize("field,value", [("is_active", 0), ("auth_provider", "google")])
def test_ineligible_accounts_get_generic_response_without_email(recovery, field, value):
    client, service, mail, user, _now = recovery
    with service._connect() as conn:
        conn.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (value, user.user_id))
        conn.commit()
    assert client.post("/auth/forgot-password", json={"email": user.email}).status_code == 202
    assert not mail.messages


def test_delivery_failure_is_generic_and_invalidates_link(recovery, monkeypatch, caplog):
    client, service, mail, _user, _now = recovery

    async def fail(**kwargs):
        raise ContactDeliveryError("do not log private provider payload")

    monkeypatch.setattr(mail, "send_text_email", fail)
    assert client.post("/auth/forgot-password", json={"email": "erica@example.com"}).status_code == 202
    with service._connect() as conn:
        row = conn.execute("SELECT delivery_status, invalidated_at FROM password_reset_tokens").fetchone()
        assert row["delivery_status"] == "failed"
        assert row["invalidated_at"]
    assert "do not log private" not in caplog.text


def test_pending_account_still_requires_email_verification(recovery):
    client, service, mail, user, _now = recovery
    with service._connect() as conn:
        conn.execute("UPDATE users SET email_verification_status = 'pending' WHERE id = ?", (user.user_id,))
        conn.commit()
    token = request_token(client, mail)
    assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 200
    assert client.post("/auth/login", json={"email": user.email, "password": "new-password"}).status_code == 403


def test_disabled_recovery_preserves_credentials(recovery, monkeypatch):
    client, _service, mail, user, _now = recovery
    token = request_token(client, mail)
    monkeypatch.setenv("AUTH_PASSWORD_RESET_ENABLED", "false")
    assert client.post("/auth/forgot-password", json={"email": user.email}).status_code == 503
    assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 503
    assert client.post("/auth/login", json={"email": user.email, "password": "old-password"}).status_code == 200


def test_daily_limit_and_other_accounts_are_preserved(recovery):
    client, service, mail, user, now = recovery
    other = service.register_user(name="Other", email="other@example.com", password="other-password")
    for _ in range(6):
        client.post("/auth/forgot-password", json={"email": user.email})
        now[0] += timedelta(minutes=1)
    assert len(mail.messages) == 5
    token = mail.messages[-1]["text"].split("#token=", 1)[1].splitlines()[0]
    assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 200
    assert service.get_user_by_token(other.token).user_id == other.user_id


def test_parallel_reset_across_service_instances_has_one_winner(recovery):
    from concurrent.futures import ThreadPoolExecutor

    from app.application.access_control.access_control_password_reset import InvalidPasswordResetTokenError

    client, service, mail, _user, now = recovery
    token = request_token(client, mail)
    second = AccessControlService(state_file=service.state_file, token_secret="test", now_provider=lambda: now[0])

    def confirm(instance):
        try:
            instance.password_reset.confirm(token=token, password="new-password")
            return True
        except InvalidPasswordResetTokenError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(confirm, (service, second))) == [False, True]
    finally:
        second.close()


@pytest.mark.real_http
def test_real_http_password_reset(recovery):
    from tests.http_runtime import LoopbackClient, serve_http

    _client, _service, mail, user, _now = recovery
    with serve_http(app) as base_url, LoopbackClient(base_url=base_url) as client:
        token = request_token(client, mail)
        assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 200
        assert client.post("/auth/session/login", json={"email": user.email, "password": "new-password"}).status_code == 200
        assert client.get("/auth/me").status_code == 200
        assert client.post("/auth/reset-password", json={"token": token, "password": "new-password"}).status_code == 400


@pytest.mark.real_http
def test_real_http_response_does_not_wait_for_email_provider(recovery, monkeypatch):
    import asyncio
    from threading import Event

    from tests.http_runtime import LoopbackClient, serve_http

    _client, _service, mail, user, _now = recovery
    release = Event()

    async def slow_provider(**kwargs):
        while not release.is_set():
            await asyncio.sleep(0.01)
        raise ContactDeliveryError("provider unavailable")

    monkeypatch.setattr(mail, "send_text_email", slow_provider)
    with serve_http(app) as base_url, LoopbackClient(base_url=base_url, timeout=2) as client:
        try:
            response = client.post("/auth/forgot-password", json={"email": user.email})
            assert response.status_code == 202
        finally:
            release.set()
