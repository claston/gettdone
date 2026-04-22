from fastapi.testclient import TestClient

from app.application import (
    ContactDeliveryError,
    ContactDeliveryResult,
    ContactMessage,
    ContactProviderNotConfiguredError,
    FileTooLargeError,
)
from app.dependencies import get_contact_service
from app.main import app


class FakeContactService:
    def __init__(self) -> None:
        self.last_message: ContactMessage | None = None
        self._next_error: Exception | None = None
        self._next_result = ContactDeliveryResult(delivery_mode="dry_run")

    def fail_with(self, error: Exception) -> None:
        self._next_error = error

    async def deliver(self, message: ContactMessage) -> ContactDeliveryResult:
        self.last_message = message
        if self._next_error is not None:
            raise self._next_error
        return self._next_result


def build_client(fake_service: FakeContactService) -> TestClient:
    app.dependency_overrides[get_contact_service] = lambda: fake_service
    return TestClient(app)


def test_contact_accepts_form_with_attachment(tmp_path) -> None:
    fake_service = FakeContactService()
    client = build_client(fake_service)

    response = client.post(
        "/contact",
        data={
            "name": "Erica",
            "email": "erica@example.com",
            "subject": "Arquivo rejeitado",
            "message": "Nao consegui converter.",
        },
        files={"attachment": ("extrato.pdf", b"%PDF test", "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["delivery_mode"] == "dry_run"
    assert fake_service.last_message is not None
    assert fake_service.last_message.name == "Erica"
    assert fake_service.last_message.attachment is not None
    assert fake_service.last_message.attachment.filename == "extrato.pdf"
    app.dependency_overrides.clear()


def test_contact_returns_503_when_provider_not_configured(tmp_path) -> None:
    fake_service = FakeContactService()
    fake_service.fail_with(ContactProviderNotConfiguredError())
    client = build_client(fake_service)

    response = client.post(
        "/contact",
        data={
            "name": "Erica",
            "email": "erica@example.com",
            "subject": "Dúvida",
            "message": "Teste",
        },
    )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    app.dependency_overrides.clear()


def test_contact_returns_502_on_provider_failure(tmp_path) -> None:
    fake_service = FakeContactService()
    fake_service.fail_with(ContactDeliveryError("gateway timeout"))
    client = build_client(fake_service)

    response = client.post(
        "/contact",
        data={
            "name": "Erica",
            "email": "erica@example.com",
            "subject": "Dúvida",
            "message": "Teste",
        },
    )

    assert response.status_code == 502
    assert "failed to deliver" in response.json()["detail"].lower()
    app.dependency_overrides.clear()


def test_contact_returns_413_on_large_attachment(tmp_path) -> None:
    fake_service = FakeContactService()
    fake_service.fail_with(FileTooLargeError())
    client = build_client(fake_service)

    response = client.post(
        "/contact",
        data={
            "name": "Erica",
            "email": "erica@example.com",
            "subject": "Dúvida",
            "message": "Teste",
        },
        files={"attachment": ("extrato.pdf", b"%PDF test", "application/pdf")},
    )

    assert response.status_code == 413
    assert "2 mb" in response.json()["detail"].lower()
    app.dependency_overrides.clear()


def test_contact_validates_email(tmp_path) -> None:
    fake_service = FakeContactService()
    client = build_client(fake_service)

    response = client.post(
        "/contact",
        data={
            "name": "Erica",
            "email": "invalid-email",
            "subject": "Dúvida",
            "message": "Teste",
        },
    )

    assert response.status_code == 400
    assert "valid email" in response.json()["detail"].lower()
    app.dependency_overrides.clear()
