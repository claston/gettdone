"""Payment link acceptance over TCP with a local email provider, without real mail."""

import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.application.contact_service import ContactService
from app.dependencies import get_contact_service
from app.main import app
from tests.http_runtime import LoopbackClient
from tests.test_auth_session_http_real_api import _run_http_server

pytestmark = pytest.mark.real_http


@contextmanager
def payment_email_provider():
    messages = []
    state = {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            assert self.path == "/emails"
            assert self.headers["Authorization"] == "Bearer fictional-payment-key"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if state["status"] == 200:
                messages.append(payload)
                response = {"id": f"fictional-payment-{len(messages)}"}
            else:
                response = {"message": "Fictional provider failure"}
            raw = json.dumps(response).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", messages, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_payment_link_delivery_failure_and_retry_over_real_http():
    with payment_email_provider() as (provider_url, messages, provider), _run_http_server(
        admin_emails={"admin@example.com"},
    ) as (base_url, access):
        access.register_user(name="Admin", email="admin@example.com", password="strong-pass")
        customer = access.register_user(name="Cliente de teste", email="checkout-customer@example.com", password="strong-pass")
        app.dependency_overrides[get_contact_service] = lambda: ContactService(
            api_key="fictional-payment-key", from_email="billing@example.com", to_email="support@example.com",
            dry_run=False, resend_api_base=provider_url,
        )
        with LoopbackClient(base_url=base_url, timeout=10) as client:
            created = client.post("/checkout/intents", json={
                "user_token": customer.token, "plan_code": "profissional", "name": "Cliente de teste",
                "email": "checkout-customer@example.com", "whatsapp": "11999990000", "accepted_terms": True,
            })
            assert created.status_code == 202, created.text
            intent_id = created.json()["intent_id"]
            path = f"/admin/checkout/intents/{intent_id}/payment-link"
            body = {"payment_link": "https://pay.example.com/fictional-order?ref=123&plan=pro"}
            assert client.post(path, json=body).status_code == 401
            assert len(messages) == 2
            assert client.post("/admin/auth/login", json={
                "email": "admin@example.com", "password": "strong-pass",
            }).status_code == 200

            sent = client.post(path, json=body)
            assert sent.status_code == 200, sent.text
            assert sent.json()["status"] == "AWAITING_PAYMENT"
            assert sent.json()["payment_link_sent_at"]
            assert len(messages) == 3
            email = messages[-1]
            assert email["to"] == ["checkout-customer@example.com"]
            assert email["from"] == "billing@example.com"
            assert email["reply_to"] == "support@example.com"
            assert "Profissional" in email["subject"]
            assert "R$ 39,90/mês" in email["text"]
            assert body["payment_link"] in email["text"]
            assert intent_id in email["text"]
            assert "<html" in email["html"]
            assert "&amp;plan=pro" in email["html"]

            before = access.read_checkout_intent_by_id(intent_id=intent_id)
            history_before = client.get(f"/admin/checkout/intents/{intent_id}/history").json()
            provider["status"] = 503
            replacement = {"payment_link": "https://pay.example.com/replacement"}
            failed = client.post(path, json=replacement)
            assert failed.status_code == 502, failed.text
            assert len(messages) == 3
            assert access.read_checkout_intent_by_id(intent_id=intent_id) == before
            events = client.get(f"/admin/checkout/intents/{intent_id}/history").json()["items"]
            assert [e for e in events if e["event_type"] != "NOTIFICATION_FAILED"] == history_before["items"]

            provider["status"] = 200
            assert client.post(path, json=replacement).status_code == 200
            assert len(messages) == 4
            assert replacement["payment_link"] in messages[-1]["text"]
            history = client.get(f"/admin/checkout/intents/{intent_id}/history").json()["items"]
            assert sum(item["event_type"] == "PAYMENT_LINK_SENT" for item in history) == 2

            # /convert is the supported upload API; /analyze is intentionally disabled.
            converted = client.post("/convert", files={
                "file": ("fictional.csv", b"date,description,amount\n2026-11-12,TESTE,40.00\n", "text/csv"),
            })
            assert converted.status_code == 200, converted.text
            assert client.get(f"/report/{converted.json()['processing_id']}").status_code == 200
            assert client.get("/report/nonexistent-payment-test").status_code == 404
            assert client.post("/analyze", files={
                "file": ("fictional.csv", b"date,description,amount\n2026-11-12,TESTE,40.00\n", "text/csv"),
            }).status_code == 405
def test_guest_purchase_release_first_access_over_real_http():
    with payment_email_provider() as (provider_url, messages, _), _run_http_server(
        admin_emails={"admin@example.com"},
    ) as (base_url, access):
        access.register_user(name="Admin", email="admin@example.com", password="strong-pass")
        app.dependency_overrides[get_contact_service] = lambda: ContactService(
            api_key="fictional-payment-key", from_email="billing@example.com", to_email="support@example.com",
            dry_run=False, resend_api_base=provider_url)
        with LoopbackClient(base_url=base_url, timeout=10) as guest, LoopbackClient(base_url=base_url, timeout=10) as admin:
            response = guest.post("/checkout/intents", json={"plan_code": "profissional", "name": "Cliente",
                "email": "guest@example.com", "whatsapp": "11912345678", "document": "123.456.789-00",
                "notes": "Preciso de NF", "accepted_terms": True})
            assert response.status_code == 202
            intent_id = response.json()["intent_id"]
            assert "123.456.789-00" in messages[0]["html"] and "Preciso de NF" in messages[0]["text"]
            path = f"/admin/checkout/intents/{intent_id}"
            assert guest.post(path + "/release", json={"create_account": True}).status_code == 401
            assert admin.post("/admin/auth/login", json={"email": "admin@example.com", "password": "strong-pass"}).status_code == 200
            assert admin.post(path + "/release").status_code == 409
            assert admin.post(path + "/payment-link", json={"payment_link": "https://pay.example/guest"}).status_code == 200
            payment_token = re.search(r"#token=([\w-]+)", messages[-1]["text"])[1]
            assert guest.post("/checkout/payment-page", json={"token": payment_token}).json()["payment_link"] == "https://pay.example/guest"
            released = admin.post(path + "/release", json={"create_account": True})
            assert released.status_code == 200 and released.json()["notification_status"] == "sent"
            setup_token = re.search(r"#token=([\w-]+)", messages[-1]["text"])[1]
            assert guest.get("/primeiro-acesso.html").headers["cache-control"] == "no-store"
            assert guest.post("/checkout/payment-page", json={"token": payment_token}).status_code == 400
            setup = guest.post("/auth/initial-access", json={"token": setup_token, "password": "secure-password", "accepted_terms": True})
            assert setup.status_code == 200 and "set-cookie" not in setup.headers
            assert access.authenticate_user("guest@example.com", "secure-password")
