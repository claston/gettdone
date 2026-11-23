"""Exercise campaign orders over TCP with a disabled external email provider."""
from datetime import timedelta

import pytest

from app.application.contact_service import ContactService
from app.dependencies import get_contact_service
from app.main import app
from tests.http_runtime import LoopbackClient
from tests.test_auth_session_http_real_api import _run_http_server
from tests.test_black_friday import END, OFFERS, START

pytestmark = pytest.mark.real_http


def test_campaign_checkout_over_real_http():
    with _run_http_server() as (base_url, service):
        clock = [START]
        service.now_provider = lambda: clock[0]
        app.dependency_overrides[get_contact_service] = lambda: ContactService(
            api_key="", from_email="test@example.test", to_email="test@example.test", dry_run=True,
        )
        with LoopbackClient(base_url=base_url, timeout=10) as client:
            response = client.get("/plans")
            assert response.status_code == 200
            assert {item["code"] for item in response.json()["items"]} == set(OFFERS)
            for code, (_, price, _, period) in OFFERS.items():
                response = client.post("/checkout/intents", json={
                    "plan_code": code, "name": "Cliente", "email": f"{code}@example.test",
                    "whatsapp": "11999990000", "accepted_terms": True,
                })
                assert response.status_code == 202, response.text
                assert response.json()["price_cents"] == price
                assert response.json()["billing_period"] == period
            clock[0] = END
            refused = client.post("/checkout/intents", json={
                "plan_code": "black_friday_3_meses", "name": "Cliente", "email": "late@example.test",
                "whatsapp": "11999990000", "accepted_terms": True,
            })
            assert refused.status_code == 400
            assert "25/11" in refused.json()["detail"]
            assert not set(OFFERS).intersection(item["code"] for item in client.get("/plans").json()["items"])
            clock[0] = START - timedelta(seconds=1)
            assert client.get("/plans").headers["cache-control"] == "public, max-age=0, must-revalidate"
