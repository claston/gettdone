import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.application import ContactDeliveryError, ContactDeliveryResult, ContactProviderNotConfiguredError
from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service, get_contact_service
from app.main import app


class FakeContactService:
    def __init__(self) -> None:
        self.support_email = "support@ofxsimples.com"
        self.sent_emails: list[dict[str, str]] = []
        self._next_error: Exception | None = None

    def fail_with(self, error: Exception) -> None:
        self._next_error = error

    async def send_text_email(
        self,
        *,
        to_email: str,
        subject: str,
        text: str,
        reply_to: str | None = None,
    ) -> ContactDeliveryResult:
        if self._next_error is not None:
            raise self._next_error
        self.sent_emails.append(
            {
                "to_email": to_email,
                "subject": subject,
                "text": text,
                "reply_to": reply_to or "",
            }
        )
        return ContactDeliveryResult(delivery_mode="dry_run")


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


def build_client(fake_contact: FakeContactService) -> tuple[TestClient, AccessControlService]:
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state.json"),
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    return TestClient(app), access_control


def _create_user_token(service: AccessControlService) -> str:
    registered = service.register_user(name="Erica Souza", email="erica@example.com", password="strong-pass")
    return registered.token


def _create_checkout_intent(client: TestClient, user_token: str, plan_code: str = "profissional") -> dict:
    response = client.post(
        "/checkout/intents",
        json={
            "user_token": user_token,
            "plan_code": plan_code,
            "name": "Erica Souza",
            "email": "erica@example.com",
            "whatsapp": "+55 11 99999-1111",
            "document": "123.456.789-00",
            "notes": "Please contact me on WhatsApp",
            "accepted_terms": True,
        },
    )
    assert response.status_code == 202
    return response.json()


def _create_admin_user(service: AccessControlService) -> str:
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    return admin.token


def test_checkout_creates_intent_and_sends_admin_and_customer_emails() -> None:
    fake_contact = FakeContactService()
    client, service = build_client(fake_contact)

    try:
        user_token = _create_user_token(service)
        payload = _create_checkout_intent(client, user_token=user_token)
        assert payload["status"] == "REQUESTED"
        assert payload["next_step"] == "SEND_PAYMENT_LINK"
        assert payload["plan_code"] == "profissional"
        assert payload["price_cents"] == 3990
        assert payload["admin_delivery_mode"] == "dry_run"
        assert payload["customer_delivery_mode"] == "dry_run"
        assert payload["intent_id"].startswith("chk_")
        assert len(fake_contact.sent_emails) == 2
        assert fake_contact.sent_emails[0]["to_email"] == "support@ofxsimples.com"
        assert fake_contact.sent_emails[1]["to_email"] == "erica@example.com"
    finally:
        app.dependency_overrides.clear()


def test_checkout_requires_user_token() -> None:
    fake_contact = FakeContactService()
    client, _service = build_client(fake_contact)
    try:
        response = client.post(
            "/checkout/intents",
            json={
                "user_token": "",
                "plan_code": "essencial",
                "name": "Erica Souza",
                "email": "erica@example.com",
                "whatsapp": "+55 11 99999-1111",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 400
        assert "user_token" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_checkout_requires_terms_acceptance() -> None:
    fake_contact = FakeContactService()
    client, service = build_client(fake_contact)
    try:
        user_token = _create_user_token(service)
        response = client.post(
            "/checkout/intents",
            json={
                "user_token": user_token,
                "plan_code": "essencial",
                "name": "Erica Souza",
                "email": "erica@example.com",
                "whatsapp": "+55 11 99999-1111",
                "accepted_terms": False,
            },
        )
        assert response.status_code == 400
        assert "terms" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_checkout_rejects_duplicate_open_order_for_same_plan() -> None:
    fake_contact = FakeContactService()
    client, service = build_client(fake_contact)
    try:
        user_token = _create_user_token(service)
        _create_checkout_intent(client, user_token=user_token, plan_code="profissional")
        response = client.post(
            "/checkout/intents",
            json={
                "user_token": user_token,
                "plan_code": "profissional",
                "name": "Erica Souza",
                "email": "erica@example.com",
                "whatsapp": "+55 11 99999-1111",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 400
        assert "open order" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_checkout_status_read_and_admin_payment_link_update(monkeypatch) -> None:
    fake_contact = FakeContactService()
    client, service = build_client(fake_contact)
    monkeypatch.setenv("PLANS_ADMIN_TOKEN", "pricing-admin-secret")
    try:
        user_token = _create_user_token(service)
        created = _create_checkout_intent(client, user_token=user_token, plan_code="essencial")
        intent_id = created["intent_id"]

        first_status = client.get("/checkout/intents/" + intent_id, params={"user_token": user_token})
        assert first_status.status_code == 200
        assert first_status.json()["status"] == "REQUESTED"
        assert first_status.json()["next_step"] == "SEND_PAYMENT_LINK"

        update = client.post(
            f"/admin/checkout/intents/{intent_id}/payment-link",
            headers={"x-admin-token": "pricing-admin-secret"},
            json={"payment_link": "https://pay.example.com/order/chk_123"},
        )
        assert update.status_code == 200
        updated_payload = update.json()
        assert updated_payload["status"] == "AWAITING_PAYMENT"
        assert updated_payload["next_step"] == "WAIT_FOR_PAYMENT"
        assert updated_payload["payment_link"] == "https://pay.example.com/order/chk_123"

        second_status = client.get("/checkout/intents/" + intent_id, params={"user_token": user_token})
        assert second_status.status_code == 200
        assert second_status.json()["status"] == "AWAITING_PAYMENT"

        latest_status = client.get("/checkout/intents/latest", params={"user_token": user_token})
        assert latest_status.status_code == 200
        assert latest_status.json()["intent_id"] == intent_id
        assert latest_status.json()["status"] == "AWAITING_PAYMENT"
    finally:
        app.dependency_overrides.clear()


def test_admin_checkout_intents_list_filters_open_with_admin_user_token() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        created = _create_checkout_intent(client, user_token=user_token, plan_code="essencial")

        update = client.post(
            f"/admin/checkout/intents/{created['intent_id']}/payment-link",
            headers={"authorization": f"Bearer {admin_token}"},
            json={"payment_link": "https://pay.example.com/order/chk_test"},
        )
        assert update.status_code == 200

        response = client.get(
            "/admin/checkout/intents",
            params={"status": "open", "limit": 10},
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) >= 1
        first = payload["items"][0]
        assert first["intent_id"] == created["intent_id"]
        assert first["status"] == "AWAITING_PAYMENT"
        assert first["next_step"] == "WAIT_FOR_PAYMENT"
        assert first["customer_email"] == "erica@example.com"
        assert first["plan_code"] == "essencial"
    finally:
        app.dependency_overrides.clear()


def test_admin_release_checkout_intent_activates_plan() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        user = access_control.get_user_by_token(user_token=user_token)
        created = _create_checkout_intent(client, user_token=user_token, plan_code="profissional")

        response = client.post(
            f"/admin/checkout/intents/{created['intent_id']}/release",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "RELEASED_FOR_USE"
        assert payload["next_step"] == "READY_TO_USE"
        assert payload["released_at"] is not None

        identity = access_control.resolve_identity(anonymous_fingerprint=None, user_token=user_token)
        assert identity.plan_code == "profissional"
        assert identity.quota_limit == 300

        intent = access_control.read_checkout_intent_by_id(intent_id=created["intent_id"])
        assert intent is not None
        assert intent["status"] == "RELEASED_FOR_USE"
        assert str(intent["user_id"]) == user.user_id
    finally:
        app.dependency_overrides.clear()


def test_admin_release_checkout_intent_without_user_id_uses_customer_email() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state-legacy-release.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        user = access_control.get_user_by_token(user_token=user_token)
        intent_id = "chk_legacy_without_user_id"

        with access_control._connect() as conn:
            access_control._execute(
                conn,
                """
                INSERT INTO checkout_intents (
                  id,
                  created_at,
                  updated_at,
                  status,
                  user_id,
                  plan_code,
                  plan_name,
                  price_cents,
                  currency,
                  billing_period,
                  customer_name,
                  customer_email,
                  customer_whatsapp,
                  customer_document,
                  customer_notes,
                  payment_link,
                  payment_link_sent_at,
                  released_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    "2026-05-05T12:00:00+00:00",
                    "2026-05-05T12:00:00+00:00",
                    "AWAITING_PAYMENT",
                    None,
                    "essencial",
                    "Essencial",
                    2990,
                    "BRL",
                    "monthly",
                    "Erica Souza",
                    user.email,
                    "+55 11 99999-0000",
                    None,
                    None,
                    "https://pay.example.com/order/chk_legacy_without_user_id",
                    "2026-05-05T12:00:00+00:00",
                    None,
                ),
            )
            conn.commit()

        response = client.post(
            f"/admin/checkout/intents/{intent_id}/release",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "RELEASED_FOR_USE"
        assert payload["intent_id"] == intent_id

        identity = access_control.resolve_identity(anonymous_fingerprint=None, user_token=user_token)
        assert identity.plan_code == "essencial"
        assert identity.quota_limit == 150
    finally:
        app.dependency_overrides.clear()


def test_admin_checkout_history_returns_order_and_manual_events() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        created = _create_checkout_intent(client, user_token=user_token, plan_code="profissional")
        intent_id = created["intent_id"]

        send_link = client.post(
            f"/admin/checkout/intents/{intent_id}/payment-link",
            headers={"authorization": f"Bearer {admin_token}"},
            json={"payment_link": "https://pay.example.com/order/chk_history"},
        )
        assert send_link.status_code == 200

        release = client.post(
            f"/admin/checkout/intents/{intent_id}/release",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert release.status_code == 200

        history = client.get(
            f"/admin/checkout/intents/{intent_id}/history",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert history.status_code == 200
        payload = history.json()
        assert payload["intent_id"] == intent_id
        event_types = {item["event_type"] for item in payload["items"]}
        assert "ORDER_REQUESTED" in event_types
        assert "PAYMENT_LINK_SENT" in event_types
        assert "PLAN_RELEASED" in event_types
    finally:
        app.dependency_overrides.clear()


def test_admin_checkout_list_supports_query_and_offset() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        _create_checkout_intent(client, user_token=user_token, plan_code="essencial")
        _create_checkout_intent(client, user_token=user_token, plan_code="escritorio")
        set_link = client.post(
            "/admin/checkout/intents/chk_does_not_exist/payment-link",
            headers={"authorization": f"Bearer {admin_token}"},
            json={"payment_link": "https://pay.example.com/none"},
        )
        assert set_link.status_code == 404

        listed = client.get(
            "/admin/checkout/intents",
            params={"status": "all", "query": "erica@example.com", "limit": 1, "offset": 0},
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert listed.status_code == 200
        first_page = listed.json()
        assert first_page["total"] >= 1
        assert first_page["limit"] == 1
        assert first_page["offset"] == 0
        assert len(first_page["items"]) == 1

        second_page = client.get(
            "/admin/checkout/intents",
            params={"status": "all", "query": "erica@example.com", "limit": 1, "offset": 1},
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert second_page.status_code == 200
        assert second_page.json()["offset"] == 1
    finally:
        app.dependency_overrides.clear()


def test_checkout_latest_fallbacks_to_legacy_email_record() -> None:
    fake_contact = FakeContactService()
    client, service = build_client(fake_contact)
    try:
        user_token = _create_user_token(service)
        user = service.get_user_by_token(user_token=user_token)
        with service._connect() as conn:
            service._execute(
                conn,
                """
                INSERT INTO checkout_intents (
                  id,
                  created_at,
                  updated_at,
                  status,
                  user_id,
                  plan_code,
                  plan_name,
                  price_cents,
                  currency,
                  billing_period,
                  customer_name,
                  customer_email,
                  customer_whatsapp,
                  customer_document,
                  customer_notes,
                  payment_link,
                  payment_link_sent_at,
                  released_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "chk_legacy_email_only",
                    "2026-05-05T12:00:00+00:00",
                    "2026-05-05T12:00:00+00:00",
                    "REQUESTED",
                    None,
                    "essencial",
                    "Essencial",
                    2990,
                    "BRL",
                    "monthly",
                    "Erica Souza",
                    user.email,
                    "+55 11 99999-0000",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            conn.commit()

        response = client.get("/checkout/intents/latest", params={"user_token": user_token})
        assert response.status_code == 200
        payload = response.json()
        assert payload["intent_id"] == "chk_legacy_email_only"
        assert payload["status"] == "REQUESTED"
    finally:
        app.dependency_overrides.clear()


def test_checkout_latest_prioritizes_awaiting_payment_with_link() -> None:
    fake_contact = FakeContactService()
    access_control = _AccessControlServiceInMemory(
        state_file=Path("backend/tmp/checkout-access-control-state-admin-priority.json"),
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_contact_service] = lambda: fake_contact
    client = TestClient(app)
    try:
        admin_token = _create_admin_user(access_control)
        user_token = _create_user_token(access_control)
        created_first = _create_checkout_intent(client, user_token=user_token, plan_code="essencial")
        first_intent_id = created_first["intent_id"]

        set_link = client.post(
            f"/admin/checkout/intents/{first_intent_id}/payment-link",
            headers={"authorization": f"Bearer {admin_token}"},
            json={"payment_link": "https://pay.example.com/order/chk_priority"},
        )
        assert set_link.status_code == 200

        _create_checkout_intent(client, user_token=user_token, plan_code="profissional")
        latest = client.get("/checkout/intents/latest", params={"user_token": user_token})
        assert latest.status_code == 200
        payload = latest.json()
        assert payload["intent_id"] == first_intent_id
        assert payload["status"] == "AWAITING_PAYMENT"
        assert payload["payment_link"] == "https://pay.example.com/order/chk_priority"
    finally:
        app.dependency_overrides.clear()


def test_checkout_returns_503_when_contact_provider_not_configured() -> None:
    fake_contact = FakeContactService()
    fake_contact.fail_with(ContactProviderNotConfiguredError())
    client, service = build_client(fake_contact)
    try:
        user_token = _create_user_token(service)
        response = client.post(
            "/checkout/intents",
            json={
                "user_token": user_token,
                "plan_code": "essencial",
                "name": "Erica Souza",
                "email": "erica@example.com",
                "whatsapp": "+55 11 99999-1111",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_checkout_returns_502_when_contact_provider_fails() -> None:
    fake_contact = FakeContactService()
    fake_contact.fail_with(ContactDeliveryError("provider timeout"))
    client, service = build_client(fake_contact)
    try:
        user_token = _create_user_token(service)
        response = client.post(
            "/checkout/intents",
            json={
                "user_token": user_token,
                "plan_code": "essencial",
                "name": "Erica Souza",
                "email": "erica@example.com",
                "whatsapp": "+55 11 99999-1111",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 502
        assert "failed" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
