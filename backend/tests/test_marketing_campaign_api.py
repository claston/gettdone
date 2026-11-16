import asyncio
import re
from datetime import timedelta

import pytest

from app.application.errors import ContactDeliveryError, ContactDeliveryRejectedError, ContactProviderNotConfiguredError
from app.application.marketing_campaign_service import MarketingCampaignConflict, MarketingCampaignService
from app.application.marketing_email_service import MarketingEmailService
from tests.test_marketing_email_api import login, marketing  # noqa: F401

BASE = "/admin/marketing/statement-guide"


@pytest.fixture
def campaign(request):
    client, access, user, mailer = request.getfixturevalue("marketing")
    mailer.require_live_sender = lambda: "OFX Simples <novidades@example.com>"
    return client, access, user, mailer


def prepare(client, count=1):
    return client.post(BASE + "/prepare", json={"expected_eligible": count})


def advance(client):
    return client.post(BASE + "/send-next", json={"confirmed": True})


def test_manual_campaign_preview_confirmation_delivery_and_persistent_deduplication(campaign):
    client, access, user, mailer = campaign
    assert client.get(BASE).status_code == 401
    assert prepare(client).status_code == 401
    assert advance(client).status_code == 401
    login(client)
    preview = client.get(BASE)
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] == 1
    assert preview.json()["prepared"] is False
    assert not mailer.messages
    assert prepare(client, 2).status_code == 409
    assert not client.get(BASE).json()["prepared"]
    assert prepare(client).status_code == 200
    assert prepare(client).json()["total"] == 1
    assert not mailer.messages
    assert client.post(BASE + "/send-next", json={"confirmed": False}).status_code == 400
    response = advance(client)
    assert response.status_code == 200, response.text
    assert response.json()["sent"] == 1
    assert response.json()["pending"] == 0
    message = mailer.messages[0]
    assert message["subject"] == "Antes de converter: 4 detalhes para conferir no seu extrato"
    assert message["to_email"] == user.email
    assert "Conhecer os planos" not in message["html"]
    assert message["idempotency_key"].startswith("marketing-campaign/")
    path = re.search(r"https://[^\s]+/marketing/unsubscribe\?token=[^\s]+", message["text"])[0]
    path = path.removeprefix("https://www.ofxsimples.com.br")
    assert path in message["html"]
    assert path in message["headers"]["List-Unsubscribe"]
    assert client.post(path).status_code == 200
    assert prepare(client).json()["sent"] == 1
    assert advance(client).json()["sent"] == 1
    assert len(mailer.messages) == 1
    with access._connect() as conn:
        assert access._fetchone(conn, "SELECT status FROM marketing_campaign_deliveries")["status"] == "sent"


@pytest.mark.parametrize("column,value", [
    ("product_updates_opt_in", 0), ("is_active", 0), ("email_verification_status", "pending"),
    ("product_updates_consent_version", 1), ("product_updates_consent_version", None),
])
def test_campaign_excludes_ineligible_before_snapshot_and_rechecks_before_send(campaign, column, value):
    client, access, user, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200
    with access._connect() as conn:
        access._execute(conn, f"UPDATE users SET {column} = ? WHERE id = ?", (value, user.user_id))
        conn.commit()
    assert client.get(BASE).json()["eligible"] == 0
    response = advance(client)
    assert response.json()["skipped"] == 1
    assert not mailer.messages


def test_campaign_retry_freezes_payload_and_stops_after_idempotency_window(campaign, monkeypatch):
    client, access, _, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200
    attempts = []
    original = mailer.send_text_email

    async def timeout_after_acceptance(**message):
        attempts.append(message)
        raise ContactDeliveryError("ambiguous timeout")

    mailer.send_text_email = timeout_after_acceptance
    assert advance(client).status_code == 502
    monkeypatch.setenv("MARKETING_SENDER_COMPANY", "Changed synthetic sender")
    assert advance(client).status_code == 502
    assert attempts[0] == attempts[1]
    now = access.now_provider()
    access.now_provider = lambda: now + timedelta(hours=24)
    mailer.send_text_email = original
    login(client)
    result = advance(client)
    assert result.status_code == 200
    assert result.json()["review"] == 1
    assert result.json()["review_items"][0]["email"] == "marketing-test@example.com"
    assert not mailer.messages


def test_campaign_resume_after_provider_error_uses_same_message(campaign):
    client, _, _, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200
    mailer.error = ContactDeliveryError("temporary provider failure")
    assert advance(client).status_code == 502
    mailer.error = None
    assert advance(client).json()["sent"] == 1
    assert advance(client).json()["sent"] == 1
    assert len(mailer.messages) == 1


def test_campaign_refuses_simulated_or_unconfigured_delivery(campaign):
    client, _, _, mailer = campaign
    login(client)

    def invalid_sender():
        raise ContactProviderNotConfiguredError

    mailer.require_live_sender = invalid_sender
    assert prepare(client).status_code == 503
    assert not client.get(BASE).json()["prepared"]
    assert not mailer.messages


def test_campaign_snapshot_excludes_new_contacts_and_rechecks_later_recipient(campaign):
    client, access, user, mailer = campaign
    second = access.register_user(name="Segundo", email="second@example.com", password="strong-pass",
                                  product_updates_opt_in=True, product_updates_consent_version=2,
                                  product_updates_opted_in_at="2026-11-15T00:00:00+00:00")
    login(client)
    assert prepare(client, 2).json()["total"] == 2
    access.register_user(name="Novo", email="new@example.com", password="strong-pass", product_updates_opt_in=True,
                         product_updates_consent_version=2, product_updates_opted_in_at="2026-11-15T01:00:00+00:00")
    assert advance(client).json()["sent"] == 1
    remaining_id = second.user_id if mailer.messages[0]["to_email"] == user.email else user.user_id
    with access._connect() as conn:
        access._execute(conn, "UPDATE users SET product_updates_opt_in = 0 WHERE id = ?", (remaining_id,))
        conn.commit()
    result = advance(client).json()
    assert (result["total"], result["sent"], result["skipped"]) == (2, 1, 1)
    assert len(mailer.messages) == 1


def test_campaign_lease_blocks_concurrent_sender_and_recovers_after_crash(campaign):
    client, access, _, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        original = mailer.send_text_email

        async def slow_send(**message):
            entered.set()
            await release.wait()
            return await original(**message)

        mailer.send_text_email = slow_send
        first = MarketingCampaignService(access, "first-admin")
        second = MarketingCampaignService(access, "second-admin")
        task = asyncio.create_task(first.send_next(mailer=mailer))
        await entered.wait()
        try:
            with pytest.raises(MarketingCampaignConflict):
                await second.send_next(mailer=mailer)
        finally:
            release.set()
            await task

    asyncio.run(exercise())
    assert len(mailer.messages) == 1
    with access._connect() as conn:
        access._execute(conn, "UPDATE marketing_campaigns SET lease_token = 'crashed', lease_until = ?",
                        ((access.now_provider() - timedelta(minutes=1)).isoformat(),))
        conn.commit()
    assert advance(client).json()["sent"] == 1
    assert len(mailer.messages) == 1


def test_campaign_does_not_retry_changed_identity_after_ambiguous_result(campaign):
    client, access, user, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200
    mailer.error = ContactDeliveryError("unknown acceptance")
    assert advance(client).status_code == 502
    with access._connect() as conn:
        access._execute(conn, "UPDATE users SET email = ? WHERE id = ?", ("changed@example.com", user.user_id))
        conn.commit()
    mailer.error = None
    assert advance(client).json()["review"] == 1
    assert not mailer.messages


def test_campaign_missing_migration_does_not_break_individual_test(campaign, monkeypatch):
    client, access, _, mailer = campaign
    login(client)
    monkeypatch.setattr(access.db, "table_exists", lambda *_args: False)
    assert client.get(BASE).status_code == 503
    assert prepare(client).status_code == 503
    assert advance(client).status_code == 503
    asyncio.run(MarketingEmailService(access).send_test(
        mailer=mailer, public_base_url="https://www.ofxsimples.com.br", request_id="test-only",
    ))
    assert len(mailer.messages) == 1


def test_rejection_then_timeout_starts_new_uncertain_window_and_later_rejection_keeps_it(campaign):
    client, access, _, mailer = campaign
    start = access.now_provider()
    login(client)
    assert prepare(client).status_code == 200
    mailer.error = ContactDeliveryRejectedError("daily quota")
    assert advance(client).status_code == 502
    access.now_provider = lambda: start + timedelta(hours=24)
    login(client)
    mailer.error = ContactDeliveryError("timeout")
    assert advance(client).status_code == 502
    with access._connect() as conn:
        first = access._fetchone(conn, "SELECT first_attempt_at FROM marketing_campaign_deliveries")["first_attempt_at"]
    assert first == access.now_provider().isoformat()
    access.now_provider = lambda: start + timedelta(hours=25)
    login(client)
    mailer.error = ContactDeliveryRejectedError("rate limit")
    assert advance(client).status_code == 502
    with access._connect() as conn:
        assert access._fetchone(conn, "SELECT first_attempt_at FROM marketing_campaign_deliveries")["first_attempt_at"] == first
    access.now_provider = lambda: start + timedelta(hours=48)
    login(client)
    mailer.error = None
    assert advance(client).json()["review"] == 1
    assert not mailer.messages


def test_confirmed_refusal_does_not_erase_evidence_owned_by_replacement_sender(campaign):
    client, access, _, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200

    async def rejected_after_lease_replaced(**_message):
        with access._connect() as conn:
            access._execute(conn, "UPDATE marketing_campaigns SET lease_token = 'replacement'")
            conn.commit()
        raise ContactDeliveryRejectedError("quota")

    mailer.send_text_email = rejected_after_lease_replaced
    assert advance(client).status_code == 502
    with access._connect() as conn:
        assert access._fetchone(conn, "SELECT first_attempt_at FROM marketing_campaign_deliveries")["first_attempt_at"]
        assert access._fetchone(conn, "SELECT lease_token FROM marketing_campaigns")["lease_token"] == "replacement"


def test_unsubscribe_after_confirmed_rejection_skips_without_sending(campaign):
    client, access, user, mailer = campaign
    login(client)
    assert prepare(client).status_code == 200
    mailer.error = ContactDeliveryRejectedError("quota")
    assert advance(client).status_code == 502
    with access._connect() as conn:
        access._execute(conn, "UPDATE users SET product_updates_opt_in = 0 WHERE id = ?", (user.user_id,))
        conn.commit()
    mailer.error = None
    result = advance(client).json()
    assert result["skipped"] == 1 and result["review"] == 0
    assert not mailer.messages
