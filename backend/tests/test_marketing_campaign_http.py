from datetime import timedelta

import pytest

from app.application.contact_service import ContactService
from app.application.errors import ContactDeliveryError
from app.dependencies import get_contact_service
from app.main import app
from tests.admin_setup import register_admin
from tests.http_runtime import LoopbackClient
from tests.test_auth_session_http_real_api import _run_http_server
from tests.test_marketing_campaign_api import BASE
from tests.test_marketing_email_http import fake_resend_server

pytestmark = pytest.mark.real_http


def test_campaign_real_http_replays_accepted_message_after_lost_response(monkeypatch):
    monkeypatch.setenv("MARKETING_SENDER_COMPANY", "Synthetic sender")
    monkeypatch.setenv("MARKETING_SENDER_CNPJ", "00.000.000/0000-00")
    with fake_resend_server() as (provider_url, messages, requests), _run_http_server(
        admin_emails={"admin@example.com"},
    ) as (base_url, access):
        monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", base_url)
        register_admin(access, name="Admin", email="admin@example.com", password="strong-pass")
        access.register_user(name="Cliente", email="recipient@example.com", password="strong-pass",
                             product_updates_opt_in=True, product_updates_consent_version=2,
                             product_updates_opted_in_at="2026-11-15T00:00:00+00:00")
        mailer = ContactService(api_key="fictional-resend-key", from_email="teste@example.com",
                                to_email="support@example.com", dry_run=False, resend_api_base=provider_url)
        original = mailer.send_text_email

        async def lose_response(**message):
            await original(**message)
            raise ContactDeliveryError("response lost after provider acceptance")

        mailer.send_text_email = lose_response
        app.dependency_overrides[get_contact_service] = lambda: mailer
        with LoopbackClient(base_url=base_url, timeout=10) as client:
            assert client.post(BASE + "/send-next", json={"confirmed": True}).status_code == 401
            assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "strong-pass"}).status_code == 200
            assert client.get(BASE).json()["eligible"] == 1
            assert client.post(BASE + "/prepare", json={"expected_eligible": 1}).status_code == 200
            assert client.post(BASE + "/send-next", json={"confirmed": True}).status_code == 502
            mailer.send_text_email = original
            assert client.post(BASE + "/send-next", json={"confirmed": True}).json()["sent"] == 1
            assert client.post(BASE + "/send-next", json={"confirmed": True}).json()["pending"] == 0
            assert len(messages) == 1 and len(requests) == 2
            assert requests[0] == requests[1]
            assert "[TESTE]" not in messages[0]["subject"]
            url = messages[0]["headers"]["List-Unsubscribe"].strip("<>")
            assert url in messages[0]["text"] and url in messages[0]["html"]
            with LoopbackClient(timeout=10) as recipient:
                assert recipient.get(url).status_code == 200
                assert recipient.post(url).status_code == 200
            assert client.get(BASE).json()["eligible"] == 0
            converted = client.post("/convert", files={
                "file": ("fictional.csv", b"date,description,amount\n2026-11-15,TESTE,40.00\n", "text/csv"),
            })
            assert converted.status_code == 200
            assert client.get(f"/report/{converted.json()['processing_id']}").status_code == 200
            assert client.get("/report/nonexistent-campaign").status_code == 404
            assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("prior_ambiguous", [False, True])
def test_campaign_http_quota_rejection_can_resume_next_day_unless_prior_attempt_was_ambiguous(monkeypatch, prior_ambiguous):
    monkeypatch.setenv("MARKETING_SENDER_COMPANY", "Synthetic sender")
    monkeypatch.setenv("MARKETING_SENDER_CNPJ", "00.000.000/0000-00")
    failures = [(500, {"name": "application_error", "message": "Unknown outcome"})] if prior_ambiguous else []
    failures.append((429, {"name": "daily_quota_exceeded", "message": "Daily quota exceeded"}))
    with fake_resend_server(failures) as (provider_url, messages, requests), _run_http_server(
        admin_emails={"admin@example.com"},
    ) as (base_url, access):
        monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", base_url)
        register_admin(access, name="Admin", email="admin@example.com", password="strong-pass")
        access.register_user(name="Cliente", email="recipient@example.com", password="strong-pass",
                             product_updates_opt_in=True, product_updates_consent_version=2,
                             product_updates_opted_in_at="2026-11-15T00:00:00+00:00")
        mailer = ContactService(api_key="fictional-resend-key", from_email="teste@example.com", to_email="support@example.com",
                                dry_run=False, resend_api_base=provider_url)
        app.dependency_overrides[get_contact_service] = lambda: mailer
        start = access.now_provider()
        with LoopbackClient(base_url=base_url, timeout=10) as client:
            credentials = {"email": "admin@example.com", "password": "strong-pass"}
            assert client.post("/admin/auth/login", json=credentials).status_code == 200
            assert client.post(BASE + "/prepare", json={"expected_eligible": 1}).status_code == 200
            if prior_ambiguous:
                assert client.post(BASE + "/send-next", json={"confirmed": True}).status_code == 502
            rejected = client.post(BASE + "/send-next", json={"confirmed": True})
            assert rejected.status_code == 502
            assert not messages
            with access._connect() as conn:
                row = access._fetchone(conn, "SELECT first_attempt_at, payload FROM marketing_campaign_deliveries")
                assert bool(row["first_attempt_at"]) is prior_ambiguous
                frozen_payload = row["payload"]
            access.now_provider = lambda: start + timedelta(hours=24)
            assert client.post("/admin/auth/login", json=credentials).status_code == 200
            resumed = client.post(BASE + "/send-next", json={"confirmed": True})
            assert resumed.status_code == 200, resumed.text
            result = resumed.json()
            assert (result["pending"], result["sent"], result["review"]) == ((0, 0, 1) if prior_ambiguous else (0, 1, 0))
            assert len(requests) == 2
            assert len(messages) == (0 if prior_ambiguous else 1)
            assert requests[0] == requests[1]
            with access._connect() as conn:
                assert access._fetchone(conn, "SELECT payload FROM marketing_campaign_deliveries")["payload"] == frozen_payload
