"""Customer order discovery and completion over a real local HTTP server."""

import pytest

from tests.http_runtime import LoopbackClient
from tests.test_auth_session_http_real_api import _run_http_server

pytestmark = pytest.mark.real_http


def test_client_open_orders_preserve_all_pending_orders_and_isolate_customers():
    with _run_http_server() as (url, service), LoopbackClient(base_url=url, timeout=10) as client:
        customer = service.register_user(name="Cliente", email="customer@example.com", password="strong-pass")
        other = service.register_user(name="Outro", email="other@example.com", password="strong-pass")

        def create(owner, code):
            return service.create_checkout_intent(
                user_id=owner.user_id, plan_code=code, customer_name="Cliente",
                customer_email=owner.email, customer_whatsapp="11999990000",
            )["id"]

        legacy = create(customer, "essencial")
        payable = create(customer, "profissional")
        released = create(customer, "escritorio")
        foreign = create(other, "essencial")
        # Retain the email fallback for historical orders with no user_id.
        with service._connect() as conn:
            service._execute(conn, "UPDATE checkout_intents SET status = 'pending', user_id = NULL WHERE id = ?", (legacy,))
            service._execute(conn, "UPDATE checkout_intents SET status = 'RELEASED_FOR_USE' WHERE id = ?", (released,))
            conn.commit()
        service.mark_checkout_intent_awaiting_payment(intent_id=payable, payment_link="https://pay.example.com/synthetic")

        assert client.get("/checkout/intents/open").status_code == 400
        assert client.get("/checkout/intents/open", params={"user_token": "invalid"}).status_code == 401
        assert client.post("/auth/session/login", json={
            "email": customer.email, "password": "strong-pass",
        }).status_code == 200
        response = client.get("/checkout/intents/open")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["intent_id"] for item in items] == [payable, legacy]
        assert items[1]["next_step"] == "SEND_PAYMENT_LINK"
        assert all("customer_email" not in item and "customer_document" not in item for item in items)
        assert foreign not in {item["intent_id"] for item in items}
        latest = client.get("/checkout/intents/latest").json()
        assert latest["intent_id"] == payable

        with service._connect() as conn:
            service._execute(conn, "UPDATE checkout_intents SET status = 'RELEASED_FOR_USE' WHERE id = ?", (payable,))
            conn.commit()
        assert client.get("/checkout/intents/latest").json()["intent_id"] == legacy
        assert [item["intent_id"] for item in client.get("/checkout/intents/open").json()["items"]] == [legacy]
        with service._connect() as conn:
            service._execute(conn, "UPDATE checkout_intents SET status = 'RELEASED_FOR_USE' WHERE id = ?", (legacy,))
            conn.commit()
        assert client.get("/checkout/intents/open").json() == {"items": []}


@pytest.mark.parametrize("latest_status", ["LOST", "RELEASED_FOR_USE"])
def test_latest_order_sorts_terminal_states_by_date_and_preserves_open_priority(latest_status):
    with _run_http_server() as (url, service), LoopbackClient(base_url=url, timeout=10) as client:
        customer = service.register_user(name="Cliente", email="customer@example.com", password="strong-pass")

        def create(code, status, date):
            intent = service.create_checkout_intent(
                user_id=customer.user_id, plan_code=code, customer_name="Cliente",
                customer_email=customer.email, customer_whatsapp="11999990000",
            )["id"]
            with service._connect() as conn:
                service._execute(conn, "UPDATE checkout_intents SET status = ?, created_at = ? WHERE id = ?",
                                 (status, date, intent))
                conn.commit()
            return intent

        earlier_status = "RELEASED_FOR_USE" if latest_status == "LOST" else "LOST"
        earlier = create("essencial", earlier_status, "2026-11-10T12:00:00+00:00")
        latest = create("profissional", latest_status, "2026-11-12T12:00:00+00:00")
        assert client.post("/auth/session/login", json={"email": customer.email, "password": "strong-pass"}).status_code == 200
        assert client.get("/checkout/intents/open").json()["items"] == []
        response = client.get("/checkout/intents/latest").json()
        assert response["intent_id"] == latest
        assert response["status"] == latest_status

        # Historical pending requests still take precedence, regardless of their age.
        pending = create("escritorio", "pending", "2026-11-09T12:00:00+00:00")
        assert client.get("/checkout/intents/latest").json()["intent_id"] == pending
        with service._connect() as conn:
            service._execute(conn, "UPDATE checkout_intents SET status = 'AWAITING_PAYMENT', payment_link = ? WHERE id = ?",
                             ("https://pay.example/fictional", earlier))
            conn.commit()
        assert client.get("/checkout/intents/latest").json()["intent_id"] == earlier
