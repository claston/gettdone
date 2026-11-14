import hashlib
import re
from datetime import timedelta

import pytest

from app.application import InvalidUserTokenError
from app.application.purchase_email_service import render_purchase_email
from app.application.purchase_flow import PurchaseFlow
from app.main import app
from tests.test_checkout_api import FakeContactService, build_client


@pytest.fixture
def purchase(monkeypatch):
    monkeypatch.setenv("WHATSAPP_SUPPORT_NUMBER", "5511911808493")
    mail = FakeContactService()
    client, service = build_client(mail)
    service.admin_emails = {"admin@example.com"}
    admin = service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    bundle = service.create_user_session(user_id=admin.user_id)
    yield client, service, mail, {"Authorization": f"Bearer {bundle.access_token}"}
    app.dependency_overrides.clear()


def order(client, **overrides):
    payload = dict(plan_code="profissional", name="Marina <Silva>", email="marina@example.com",
                   whatsapp="+55 11 91234-5678", document="123.456.789-00", notes="Preciso de NF\nObrigada!",
                   accepted_terms=True)
    payload.update(overrides)
    response = client.post("/checkout/intents", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["intent_id"]


def test_guest_order_creates_no_account_and_backoffice_receives_every_field(purchase):
    client, service, mail, _ = purchase
    intent = order(client)
    assert service._test_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert service.read_checkout_intent_by_id(intent_id=intent)["user_id"] is None
    receipt = mail.sent_emails[0]
    for value in ("Marina", "marina@example.com", "+55 11 91234-5678", "123.456.789-00", "Preciso de NF",
                  "Obrigada!", "Profissional", "39,90", "Aceite", intent):
        assert value in receipt["html"]
        assert value in receipt["text"]
    assert "&lt;Silva&gt;" in receipt["html"]
    assert "<Silva>" not in receipt["html"]
    assert "5511911808493" in mail.sent_emails[1]["html"]


def test_admin_confirmation_first_access_one_use_and_duplicate_release(purchase):
    client, service, mail, headers = purchase
    intent = order(client)
    url = f"/admin/checkout/intents/{intent}/release"
    assert client.post(url).status_code in (401, 403)
    assert client.get(url + "-preview").status_code == 401
    assert client.get(url + "-preview", headers=headers).json()["account_exists"] is False
    assert client.post(url, headers=headers).status_code == 409
    result = client.post(url, headers=headers, json={"create_account": True})
    assert result.status_code == 200, result.text
    assert result.json()["notification_status"] == "sent"
    user = service._test_conn.execute("SELECT * FROM users WHERE email = 'marina@example.com'").fetchone()
    assert not user["is_admin"] and not user["is_active"] and user["initial_access_pending"]
    assert not user["product_updates_opt_in"] and user["terms_accepted_at"] is None
    assert client.post("/admin/users/status", headers=headers,
                       json={"user_id": user["id"], "is_active": True}).status_code == 409
    with pytest.raises(InvalidUserTokenError):
        service.get_user_by_token(service._encode_token(user["id"]))
    assert service.password_reset.issue(email=user["email"]) is None
    token = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    row = service._test_conn.execute("SELECT * FROM purchase_tokens WHERE purpose = 'setup'").fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    confirm = {"token": token, "password": "minha-senha-segura", "accepted_terms": True}
    assert client.post("/auth/initial-access", json={**confirm, "accepted_terms": False}).status_code == 400
    assert client.post("/auth/initial-access", json=confirm).status_code == 200
    assert client.post("/auth/initial-access", json=confirm).status_code == 400
    assert service.authenticate_user(user["email"], confirm["password"]).user_id == user["id"]
    before = len(mail.sent_emails)
    assert client.post(url, headers=headers, json={"create_account": True}).status_code == 200
    assert len(mail.sent_emails) == before
    assert service._test_conn.execute("SELECT COUNT(*) FROM user_plan_subscriptions WHERE user_id = ?",
                                      (user["id"],)).fetchone()[0] == 1


def test_failed_release_email_keeps_plan_and_admin_can_reissue(purchase):
    client, service, mail, headers = purchase
    intent = order(client)
    mail.delivery_mode = "dry_run"
    url = f"/admin/checkout/intents/{intent}"
    response = client.post(url + "/release", headers=headers, json={"create_account": True})
    assert response.status_code == 200
    assert response.json()["status"] == "RELEASED_FOR_USE"
    assert response.json()["notification_status"] == "failed"
    old = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    mail.delivery_mode = "resend"
    assert client.post(url + "/resend-notification", headers=headers).json()["notification_status"] == "sent"
    assert client.post("/auth/initial-access", json=dict(token=old, password="12345678", accepted_terms=True)).status_code == 400


def test_payment_copy_token_is_scoped_and_current_link_is_server_controlled(purchase):
    client, service, mail, headers = purchase
    intent = order(client)
    response = client.post(f"/admin/checkout/intents/{intent}/payment-link", headers=headers,
                           json={"payment_link": "https://pay.example/123?a=1&b=2"})
    assert response.status_code == 200, response.text
    token = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    result = client.post("/checkout/payment-page", json={"token": token})
    assert result.status_code == 200
    assert result.json()["payment_link"] == "https://pay.example/123?a=1&b=2"
    assert result.headers["cache-control"] == "no-store"
    assert client.post("/checkout/payment-page", json={"token": intent}).status_code == 400
    assert client.post("/auth/initial-access", json=dict(token=token, password="12345678", accepted_terms=True)).status_code == 400


def test_existing_account_credentials_preserved_and_only_selected_order_released(purchase):
    client, service, mail, headers = purchase
    user = service.register_user(name="Marina", email="marina@example.com", password="original-pass")
    intent = order(client)
    other = order(client, plan_code="essencial")
    result = client.post(f"/admin/checkout/intents/{intent}/release", headers=headers)
    assert result.status_code == 200, result.text
    assert service.authenticate_user(user.email, "original-pass").user_id == user.user_id
    assert service.read_checkout_intent_by_id(intent_id=other)["status"] == "REQUESTED"
    assert "#token=" not in mail.sent_emails[-1]["text"]


def test_provisioning_allowlist_email_never_grants_admin(purchase):
    client, service, _, headers = purchase
    service.admin_emails.add("marina@example.com")
    intent = order(client)
    result = client.post(f"/admin/checkout/intents/{intent}/release", headers=headers, json={"create_account": True})
    assert result.status_code == 200
    assert not service._test_conn.execute("SELECT is_admin FROM users WHERE email = 'marina@example.com'").fetchone()[0]
    with service._connect() as conn:
        service._sync_admin_emails(conn)
        conn.commit()
    assert not service._test_conn.execute("SELECT is_admin FROM users WHERE email = 'marina@example.com'").fetchone()[0]


def test_suspended_account_is_not_reactivated_and_mismatched_order_is_blocked(purchase):
    client, service, _, headers = purchase
    user = service.register_user(name="Marina", email="marina@example.com", password="original-pass")
    intent = order(client)
    service.set_user_active_status(user_id=user.user_id, is_active=False)
    assert client.post(f"/admin/checkout/intents/{intent}/release", headers=headers).status_code == 409
    service.set_user_active_status(user_id=user.user_id, is_active=True)
    service._test_conn.execute("UPDATE checkout_intents SET user_id = 'usr_wrong' WHERE id = ?", (intent,))
    service._test_conn.commit()
    assert client.post(f"/admin/checkout/intents/{intent}/release", headers=headers).status_code == 409


def test_setup_expires_reissue_invalidates_and_google_cannot_bypass(purchase):
    from app.application.errors import GoogleOAuthExchangeError

    client, service, mail, headers = purchase
    intent = order(client)
    assert client.post(f"/admin/checkout/intents/{intent}/release", headers=headers,
                       json={"create_account": True}).status_code == 200
    token = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    with pytest.raises(GoogleOAuthExchangeError):
        service.register_or_authenticate_google_user(provider_user_id="google-123", email="marina@example.com", name="Buyer")
    expired = (service.now_provider() - timedelta(seconds=1)).isoformat()
    service._test_conn.execute("UPDATE purchase_tokens SET expires_at = ? WHERE purpose = 'setup'", (expired,))
    service._test_conn.commit()
    assert client.post("/auth/initial-access", json=dict(token=token, password="12345678", accepted_terms=True)).status_code == 400
    response = client.post(f"/admin/checkout/intents/{intent}/resend-notification", headers=headers)
    assert response.json()["notification_status"] == "sent"
    newer = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    assert newer != token
    assert client.post("/auth/initial-access", json=dict(token=newer, password="12345678", accepted_terms=True)).status_code == 200


@pytest.mark.parametrize("number", ["", "invalid-number", "55<script>"])
def test_invalid_whatsapp_is_omitted_and_optional_order_fields_are_explicit(purchase, monkeypatch, number):
    client, service, _, _ = purchase
    intent = order(client, document=None, notes=None)
    monkeypatch.setenv("WHATSAPP_SUPPORT_NUMBER", number)
    data = service.read_checkout_intent_by_id(intent_id=intent)
    email = render_purchase_email("customer_receipt", data)
    assert "wa.me" not in email["html"] and "WhatsApp" not in email["text"]
    admin = render_purchase_email("admin_receipt", data)
    assert "Não informado" in admin["html"] and "Não informadas" in admin["text"]


def test_release_cannot_race_payment_email_and_failure_rollback_is_atomic(purchase, monkeypatch):
    client, service, _, headers = purchase
    intent = order(client)
    flow = PurchaseFlow(service)
    sending = flow.claim_notification(intent, "payment", retry=True)
    assert client.post(f"/admin/checkout/intents/{intent}/release", headers=headers,
                       json={"create_account": True}).status_code == 409
    flow.finish_notification(sending, sent=False)

    def fail_activation(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr("app.application.purchase_flow.activate_user_plan", fail_activation)
    with pytest.raises(RuntimeError):
        flow.release(intent, create_account=True, actor_kind="admin_user", actor_user_id=None)
    assert service._test_conn.execute("SELECT id FROM users WHERE email = 'marina@example.com'").fetchone() is None
    assert service.read_checkout_intent_by_id(intent_id=intent)["status"] == "REQUESTED"


def test_secret_validation_errors_do_not_echo_passwords_or_tokens(purchase):
    client, _, _, _ = purchase
    response = client.post("/auth/initial-access", json={"token": "SECRET-TOKEN", "password": "SHORT"})
    assert response.status_code == 400
    assert "SECRET-TOKEN" not in response.text and "SHORT" not in response.text


def test_guest_retry_does_not_create_duplicate_and_payment_keeps_original_consent(purchase):
    client, service, _, headers = purchase
    intent = order(client)
    data = service.read_checkout_intent_by_id(intent_id=intent)
    duplicate = client.post("/checkout/intents", json={"plan_code": "profissional", "name": "Marina",
        "email": "marina@example.com", "whatsapp": "11999999999", "accepted_terms": True})
    assert duplicate.status_code == 400 and "open order" in duplicate.json()["detail"]
    assert service._test_conn.execute("SELECT COUNT(*) FROM checkout_intents").fetchone()[0] == 1
    old = "2026-01-01T10:00:00+00:00"
    service._test_conn.execute("UPDATE checkout_intents SET terms_accepted_at = ? WHERE id = ?", (old, intent))
    service._test_conn.commit()
    assert client.post(f"/admin/checkout/intents/{intent}/payment-link", headers=headers,
                       json={"payment_link": "https://pay.example/current"}).status_code == 200
    assert service.read_checkout_intent_by_id(intent_id=intent)["terms_accepted_at"] == old
    assert data["terms_accepted_at"]


def test_rollout_flags_and_guest_rate_limit(purchase, monkeypatch):
    client, service, _, headers = purchase
    payload = dict(plan_code="profissional", name="Marina", email="marina@example.com",
                   whatsapp="11999999999", accepted_terms=True)
    monkeypatch.setenv("CHECKOUT_GUEST_ENABLED", "false")
    assert client.post("/checkout/intents", json=payload).status_code == 503
    monkeypatch.setenv("CHECKOUT_GUEST_ENABLED", "true")
    intent = order(client)
    monkeypatch.setenv("CHECKOUT_ADMIN_PROVISIONING_ENABLED", "false")
    assert client.post(f"/admin/checkout/intents/{intent}/release", headers=headers,
                       json={"create_account": True}).status_code == 409
    assert service._test_conn.execute("SELECT id FROM users WHERE email = 'marina@example.com'").fetchone() is None
    for _ in range(9):
        assert client.post("/checkout/intents", json=payload).status_code == 400
    limited = client.post("/checkout/intents", json=payload)
    assert limited.status_code == 429 and limited.headers["retry-after"]


@pytest.mark.parametrize("authenticated_first", [True, False])
def test_open_order_is_unique_across_authenticated_and_guest_checkout(purchase, authenticated_first):
    client, service, mail, _ = purchase
    user = service.register_user(name="Marina", email="marina@example.com", password="original-pass")
    payload = dict(plan_code="profissional", name="Marina", email=" Marina@Example.com ",
                   whatsapp="11999999999", accepted_terms=True)
    authenticated = {**payload, "user_token": user.token}
    first, second = (authenticated, payload) if authenticated_first else (payload, authenticated)
    assert client.post("/checkout/intents", json=first).status_code == 202
    response = client.post("/checkout/intents", json=second)
    assert response.status_code == 400 and "open order" in response.json()["detail"]
    assert service._test_conn.execute("SELECT COUNT(*) FROM checkout_intents").fetchone()[0] == 1
    assert len(mail.sent_emails) == 2


def test_admin_suspension_revokes_all_pending_setup_links_and_requires_explicit_reissue(purchase):
    client, service, mail, headers = purchase
    intent = order(client)
    path = f"/admin/checkout/intents/{intent}"
    assert client.post(path + "/release", headers=headers, json={"create_account": True}).status_code == 200
    token = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    user_id = service.read_checkout_intent_by_id(intent_id=intent)["user_id"]
    listed = client.get("/admin/users", headers=headers).json()["items"]
    assert next(user for user in listed if user["user_id"] == user_id)["initial_access_pending"] is True
    response = client.post("/admin/users/status", headers=headers, json={"user_id": user_id, "is_active": False})
    assert response.status_code == 200, response.text
    confirm = dict(token=token, password="secure-password", accepted_terms=True)
    assert client.post("/auth/initial-access", json=confirm).status_code == 400
    assert client.post("/admin/users/status", headers=headers,
                       json={"user_id": user_id, "is_active": True}).status_code == 409
    with pytest.raises(InvalidUserTokenError):
        service.get_user_by_token(service._encode_token(user_id))
    # Repeating release must not restore the revoked invitation.
    before = len(mail.sent_emails)
    assert client.post(path + "/release", headers=headers).status_code == 200
    assert len(mail.sent_emails) == before
    assert client.post("/auth/initial-access", json=confirm).status_code == 400
    assert client.post(path + "/resend-notification", headers=headers).json()["notification_status"] == "sent"
    newer = re.search(r"#token=([\w-]+)", mail.sent_emails[-1]["text"])[1]
    assert newer != token
    assert client.post("/auth/initial-access", json={**confirm, "token": newer}).status_code == 200
