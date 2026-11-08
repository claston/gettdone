import asyncio
import json
import re
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.contact_service import ContactDeliveryResult, ContactService
from app.application.errors import ContactDeliveryError, ContactProviderNotConfiguredError
from app.dependencies import get_access_control_service, get_contact_service
from app.main import app


class FakeResend:
    def __init__(self):
        self.messages = []
        self.mode = "resend"
        self.error = None

    async def send_text_email(self, **message):
        if self.error:
            raise self.error
        self.messages.append(message)
        return ContactDeliveryResult(delivery_mode=self.mode, provider_message_id="test-message-1")


@pytest.fixture
def marketing(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_TEST_RECIPIENT", "marketing-test@example.com")
    monkeypatch.delenv("MARKETING_FROM_EMAIL", raising=False)
    service = AccessControlService(
        state_file=tmp_path / "access.json",
        token_secret="marketing-test-secret",
        admin_emails={"admin@example.com"},
    )
    service.register_user(name="Admin", email="admin@example.com", password="strong-pass")
    user = service.register_user(
        name="Teste", email="marketing-test@example.com", password="strong-pass",
        product_updates_opt_in=True,
        product_updates_opted_in_at="2026-10-29T12:00:00+00:00",
        product_updates_consent_version=2,
    )
    mailer = FakeResend()
    monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", "https://www.ofxsimples.com.br")
    app.dependency_overrides[get_access_control_service] = lambda: service
    app.dependency_overrides[get_contact_service] = lambda: mailer
    with TestClient(app) as client:
        yield client, service, user, mailer
    app.dependency_overrides.clear()
    service.close()


def login(client):
    result = client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "strong-pass"})
    assert result.status_code == 200


def send(client, request_id=None):
    return client.post("/admin/marketing/test-email", json={"request_id": str(request_id or uuid4())})


def unsubscribe_path(mailer):
    url = re.search(r"https://[^\s]+/marketing/unsubscribe\?token=[^\s]+", mailer.messages[-1]["text"]).group()
    return url.removeprefix("https://www.ofxsimples.com.br")


def test_admin_send_finishes_inline_and_includes_unsubscribe(marketing):
    client, _, _, mailer = marketing
    login(client)
    request_id = uuid4()
    response = send(client, request_id)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "recipient": "marketing-test@example.com", "template_id": "marketing-test-v1",
        "delivery_mode": "resend", "provider_message_id": "test-message-1",
    }
    assert len(mailer.messages) == 1
    message = mailer.messages[0]
    assert message["to_email"] == "marketing-test@example.com"
    assert "teste" in message["subject"].lower()
    assert "Não quero receber mais e-mails" in message["text"]
    assert message["idempotency_key"].endswith(str(request_id))
    assert message["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert unsubscribe_path(mailer) in message["headers"]["List-Unsubscribe"]


def test_send_requires_admin_and_disallows_arbitrary_recipient(marketing):
    client, _, _, mailer = marketing
    assert send(client).status_code == 401
    assert client.post("/auth/session/login", json={"email": "marketing-test@example.com", "password": "strong-pass"}).status_code == 200
    assert send(client).status_code == 403
    login(client)
    response = client.post("/admin/marketing/test-email", json={"request_id": str(uuid4()), "to_email": "other@example.com"})
    assert response.status_code == 400
    assert mailer.messages == []


@pytest.mark.parametrize("column,value", [
    ("product_updates_opt_in", 0), ("is_active", 0), ("email_verification_status", "pending"),
])
def test_ineligible_recipient_is_blocked_at_send_time(marketing, column, value):
    client, service, user, mailer = marketing
    login(client)
    with service._connect() as conn:
        service._execute(conn, f"UPDATE users SET {column} = ? WHERE id = ?", (value, user.user_id))
        conn.commit()
    assert send(client).status_code == 409
    assert mailer.messages == []


def test_missing_test_user_is_not_created(marketing):
    client, service, user, mailer = marketing
    login(client)
    with service._connect() as conn:
        service._execute(conn, "UPDATE users SET email = ? WHERE id = ?", ("other@example.com", user.user_id))
        conn.commit()
    assert send(client).status_code == 409
    assert mailer.messages == []


def test_unsubscribe_requires_confirmation_and_persists_without_login(marketing):
    client, service, user, mailer = marketing
    login(client)
    assert send(client).status_code == 200
    path = unsubscribe_path(mailer)
    client.cookies.clear()
    confirmation = client.get(path)
    assert confirmation.status_code == 200
    assert 'method="post"' in confirmation.text
    assert confirmation.headers["cache-control"] == "no-store"
    assert service.list_marketing_contacts_for_admin()[1] == 1
    assert client.post(path).status_code == 200
    assert client.post(path, data={"List-Unsubscribe": "One-Click"}).status_code == 200
    # A fresh service sees the persisted opt-out; original consent evidence is retained.
    fresh = AccessControlService(state_file=service.state_file, token_secret="marketing-test-secret")
    assert fresh.list_marketing_contacts_for_admin()[1] == 0
    with fresh._connect() as conn:
        row = fresh._fetchone(conn, "SELECT product_updates_opted_in_at, product_updates_consent_version FROM users WHERE id = ?",
                              (user.user_id,))
    assert row["product_updates_opted_in_at"] == "2026-10-29T12:00:00+00:00"
    assert row["product_updates_consent_version"] == 2
    login(client)
    assert send(client).status_code == 409
    assert len(mailer.messages) == 1


def test_unsubscribe_token_cannot_authenticate_or_affect_another_user(marketing):
    client, service, user, mailer = marketing
    login(client)
    assert send(client).status_code == 200
    path = unsubscribe_path(mailer)
    token = path.split("token=", 1)[1]
    client.cookies.clear()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/marketing/unsubscribe", params={"token": user.token}).status_code == 400
    changed = path[:-1] + ("0" if path[-1] != "0" else "1")
    assert client.post(changed).status_code == 400
    assert client.get(changed).status_code == 400
    assert service.list_marketing_contacts_for_admin()[1] == 1


def test_old_link_is_invalid_after_new_consent(marketing):
    client, service, user, mailer = marketing
    login(client)
    assert send(client).status_code == 200
    path = unsubscribe_path(mailer)
    assert client.post(path).status_code == 200
    with service._connect() as conn:
        service._execute(conn, "UPDATE users SET product_updates_opt_in = ?, product_updates_opted_in_at = ? WHERE id = ?",
                         (service._true_value(), "2026-10-30T12:00:00+00:00", user.user_id))
        conn.commit()
    assert client.post(path).status_code == 400
    assert service.list_marketing_contacts_for_admin()[1] == 1


@pytest.mark.parametrize("error,status", [
    (ContactDeliveryError("secret provider response"), 502), (ContactProviderNotConfiguredError(), 503),
])
def test_delivery_error_is_visible_without_leaking_provider_data(marketing, error, status):
    client, _, _, mailer = marketing
    login(client)
    mailer.error = error
    response = send(client)
    assert response.status_code == status
    assert "secret" not in response.text


def test_dry_run_is_explicit(marketing):
    client, _, _, mailer = marketing
    login(client)
    mailer.mode = "dry_run"
    assert send(client).json()["delivery_mode"] == "dry_run"


@pytest.mark.parametrize("base_url", [
    "", "/relative", "http://example.com", "https://example.com/path",
    "https://example.com?token=bad", "https://example.com#fragment", "https://user:password@example.com",
    "https://[broken", "https://example.com:bad", "https://example.com:", "https://example.com:0", "https://@example.com",
])
def test_invalid_public_origin_blocks_delivery(marketing, monkeypatch, base_url):
    client, _, _, mailer = marketing
    login(client)
    monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", base_url)
    assert send(client).status_code == 503
    assert not mailer.messages


@pytest.mark.parametrize("raw_response", [b"not json", b"{}", b"[]", b'null', b'{"id":42}'])
def test_resend_response_without_confirmation_is_a_delivery_error(monkeypatch, raw_response):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=raw_response))
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    mailer = ContactService(api_key="fictional", from_email="test@example.com", to_email="support@example.com", dry_run=False)
    with pytest.raises(ContactDeliveryError):
        asyncio.run(mailer.send_text_email(to_email="marketing-test@example.com", subject="Teste", text="Teste"))


def test_unsubscribe_signature_is_bound_to_recipient(marketing):
    client, service, user, mailer = marketing
    other = service.register_user(name="Outro", email="other@example.com", password="strong-pass", product_updates_opt_in=True)
    login(client)
    assert send(client).status_code == 200
    path = unsubscribe_path(mailer)
    assert client.post(path.replace(user.user_id, other.user_id)).status_code == 400
    assert service.list_marketing_contacts_for_admin()[1] == 2


@pytest.mark.parametrize("recipient", [None, "", "   ", "invalid", "one@example.com,two@example.com",
                                       "Name <one@example.com>", "one@example.com\r\nBcc: two@example.com"])
def test_missing_or_invalid_recipient_configuration_blocks_delivery(marketing, monkeypatch, recipient):
    client, _, _, mailer = marketing
    login(client)
    if recipient is None:
        monkeypatch.delenv("MARKETING_TEST_RECIPIENT", raising=False)
    else:
        monkeypatch.setenv("MARKETING_TEST_RECIPIENT", recipient)
    response = send(client)
    assert response.status_code == 503
    assert "destinatário" in response.json()["detail"]
    assert mailer.messages == []


def test_configured_recipient_is_normalized_and_selected_exclusively(marketing, monkeypatch):
    client, service, original, mailer = marketing
    selected = service.register_user(
        name="Selected", email="selected@example.com", password="strong-pass",
        product_updates_opt_in=True,
    )
    monkeypatch.setenv("MARKETING_TEST_RECIPIENT", "  SELECTED@example.com  ")
    login(client)
    response = send(client)
    assert response.status_code == 200
    assert response.json()["recipient"] == "selected@example.com"
    assert [message["to_email"] for message in mailer.messages] == ["selected@example.com"]
    path = unsubscribe_path(mailer)
    assert selected.user_id in path
    assert original.user_id not in path
    assert client.post(path).status_code == 200
    assert send(client).status_code == 409
    assert len(mailer.messages) == 1
    assert service.list_marketing_contacts_for_admin()[1] == 1


def test_unsubscribe_still_works_without_recipient_configuration(marketing, monkeypatch):
    client, _, _, mailer = marketing
    login(client)
    assert send(client).status_code == 200
    path = unsubscribe_path(mailer)
    monkeypatch.delenv("MARKETING_TEST_RECIPIENT")
    assert client.get(path).status_code == 200
    assert client.post(path).status_code == 200


@pytest.mark.parametrize("marketing_sender", [None, "", "   ", "  OFX Simples <novidades@example.com>  "])
def test_marketing_sender_does_not_change_transactional_sender(marketing, monkeypatch, marketing_sender):
    client, _, _, _ = marketing
    transactional_sender = "OFX Simples <contato@example.com>"
    monkeypatch.setenv("CONTACT_FROM_EMAIL", transactional_sender)
    monkeypatch.setenv("CONTACT_RESEND_DRY_RUN", "false")
    monkeypatch.setenv("RESEND_API_KEY", "fictional-key")
    monkeypatch.setenv("CONTACT_DELIVERY_PROVIDER", "hostinger_smtp")
    if marketing_sender is not None:
        monkeypatch.setenv("MARKETING_FROM_EMAIL", marketing_sender)

    messages = []

    def capture(request):
        messages.append(json.loads(request.content))
        return httpx.Response(200, json={"id": f"fictional-message-{len(messages)}"})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(transport=httpx.MockTransport(capture), **kwargs))
    mailer = ContactService.from_env()
    app.dependency_overrides[get_contact_service] = lambda: mailer
    login(client)
    response = send(client)
    assert response.status_code == 200, response.text
    assert response.json()["delivery_mode"] == "resend"
    assert messages[0]["from"] == ((marketing_sender or "").strip() or transactional_sender)

    # The shared mailer must retain its configured sender after a marketing send.
    asyncio.run(mailer.send_text_email(to_email="account@example.com", subject="Conta", text="Mensagem transacional"))
    assert len(messages) == 2
    assert messages[1]["from"] == transactional_sender
