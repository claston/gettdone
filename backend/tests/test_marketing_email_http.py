"""Real HTTP acceptance, including the Resend wire format, without sending real mail."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from app.application.contact_service import ContactService
from app.dependencies import get_contact_service
from app.main import app
from tests.test_auth_session_http_real_api import _run_http_server

pytestmark = pytest.mark.real_http


@contextmanager
def fake_resend_server():
    messages = []
    requests = []
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            assert self.path == "/emails"
            assert self.headers["Authorization"] == "Bearer fictional-resend-key"
            key = self.headers["Idempotency-Key"]
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(key)
            if key not in seen:
                messages.append(payload)
                seen[key] = {"id": f"fictional-message-{len(messages)}"}
            raw = json.dumps(seen[key]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", messages, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_inline_send_retries_and_unsubscribe_over_real_http(monkeypatch):
    with fake_resend_server() as (provider_url, messages, requests), _run_http_server(
        admin_emails={"admin@example.com"},
    ) as (base_url, access):
        access.register_user(name="Admin", email="admin@example.com", password="strong-pass")
        access.register_user(
            name="Cliente de teste", email="a@a.com.br", password="strong-pass",
            product_updates_opt_in=True, product_updates_opted_in_at="2026-10-29T12:00:00+00:00",
            product_updates_consent_version=2,
        )
        monkeypatch.setenv("MARKETING_PUBLIC_BASE_URL", base_url)
        app.dependency_overrides[get_contact_service] = lambda: ContactService(
            api_key="fictional-resend-key", from_email="teste@example.com", to_email="support@example.com",
            dry_run=False, resend_api_base=provider_url,
        )
        with httpx.Client(base_url=base_url, timeout=10) as client:
            path = "/admin/marketing/test-email"
            body = {"request_id": str(uuid4())}
            assert client.post(path, json=body).status_code == 401
            assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "strong-pass"}).status_code == 200
            sent = client.post(path, json=body)
            assert sent.status_code == 200, sent.text
            assert sent.json()["provider_message_id"] == "fictional-message-1"
            assert client.post(path, json=body).json() == sent.json()
            assert len(requests) == 2 and len(messages) == 1
            message = messages[0]
            assert message["from"] == "teste@example.com"
            assert message["to"] == ["a@a.com.br"]
            assert message["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
            unsubscribe_url = message["headers"]["List-Unsubscribe"].strip("<>")
            assert unsubscribe_url in message["text"]
            # A supported CSV upload exercises the production replacement for /analyze.
            converted = client.post("/convert", files={
                "file": ("fictional.csv", b"date,description,amount\n2026-10-29,TESTE,40.00\n", "text/csv"),
            })
            assert converted.status_code == 200, converted.text
            assert client.get(f"/report/{converted.json()['processing_id']}").status_code == 200
            assert client.get("/report/nonexistent-marketing-test").status_code == 404
            assert client.post("/analyze").status_code == 405
            with httpx.Client(timeout=10) as recipient:
                assert recipient.get(unsubscribe_url).status_code == 200
                assert access.list_marketing_contacts_for_admin()[1] == 1
                assert recipient.post(unsubscribe_url, data={"List-Unsubscribe": "One-Click"}).status_code == 200
                assert recipient.post(unsubscribe_url).status_code == 200
            assert client.post(path, json={"request_id": str(uuid4())}).status_code == 409
            assert len(messages) == 1
            assert access.list_marketing_contacts_for_admin()[1] == 0
