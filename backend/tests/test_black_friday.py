from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.black_friday import add_months, monthly_window
from app.application.conversion.pro_conversion import ConversionProQuota, ProConversionPolicy
from app.application.errors import QuotaExceededError
from app.application.purchase_email_service import render_purchase_email
from app.application.purchase_flow import PurchaseFlow
from app.dependencies import get_access_control_service
from app.main import app

START = datetime(2026, 11, 25, 3, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, 3, tzinfo=timezone.utc)
OFFERS = {
    "essencial_black": ("Essencial Black", 1990, 600, "monthly"),
    "profissional_black": ("Profissional Black", 2990, 1000, "monthly"),
    "black_friday_3_meses": ("Black Friday 3 meses", 5990, 1000, "prepaid_quarter"),
}


@pytest.fixture
def campaign(tmp_path):
    clock = [START]
    service = AccessControlService(state_file=tmp_path / "access.json", token_secret="black-test",
                                   now_provider=lambda: clock[0])
    yield service, clock
    service.close()


def order(service, code, user=None):
    return service.create_checkout_intent(user_id=user.user_id if user else None, plan_code=code,
                                         customer_name="Cliente", customer_email="black@example.test",
                                         customer_whatsapp="11999990000")


def test_campaign_catalog_prices_boundaries_and_cache(campaign):
    service, clock = campaign
    clock[0] = START - timedelta(seconds=1)
    original = service.list_public_plans()
    assert not set(OFFERS).intersection(plan["code"] for plan in original)
    clock[0] = START
    offers = service.list_public_plans()
    assert {plan["code"] for plan in offers} == set(OFFERS)
    for plan in offers:
        assert (plan["name"], plan["price_cents"], plan["quota_limit"], plan["billing_period"]) == OFFERS[plan["code"]]
        assert plan["quota_mode"] == "pages"
        assert plan["max_upload_size_bytes"] == 10485760
    clock[0] = END - timedelta(microseconds=1)
    assert service.list_public_plans() == offers
    clock[0] = END
    assert service.list_public_plans() == original


@pytest.mark.parametrize("code", OFFERS)
@pytest.mark.parametrize("when", [START - timedelta(microseconds=1), END])
def test_checkout_refuses_promotion_outside_window(campaign, code, when):
    service, clock = campaign
    clock[0] = when
    with pytest.raises(ValueError, match="25/11.*30/11"):
        order(service, code)


@pytest.mark.parametrize("code", OFFERS)
def test_checkout_freezes_promotional_price_and_can_release_after_campaign(campaign, code):
    service, clock = campaign
    user = service.register_user(name="Cliente", email="black@example.test", password="safe-password")
    intent = order(service, code, user)
    assert intent["price_cents"] == OFFERS[code][1]
    assert intent["billing_period"] == OFFERS[code][3]
    clock[0] = END + timedelta(days=1)
    assert PurchaseFlow(service).release(intent["id"], actor_kind="system", actor_user_id=None)
    identity = service.resolve_identity(None, user.token)
    assert identity.plan_code == code
    assert identity.quota_limit == OFFERS[code][2]
    assert not PurchaseFlow(service).release(intent["id"], actor_kind="system", actor_user_id=None)


def test_three_months_monthly_quota_expiration_and_no_accumulation(campaign):
    service, clock = campaign
    user = service.register_user(name="Cliente", email="black@example.test", password="safe-password")
    intent = order(service, "black_friday_3_meses", user)
    PurchaseFlow(service).release(intent["id"], actor_kind="system", actor_user_id=None)
    identity = service.resolve_identity(None, user.token)
    service.consume_quota(identity, consumed_units=1000)
    with pytest.raises(QuotaExceededError):
        service.ensure_quota_available(identity)
    clock[0] = datetime(2026, 12, 25, 3, tzinfo=timezone.utc) - timedelta(microseconds=1)
    assert service.get_remaining_quota(identity) == 0
    clock[0] += timedelta(microseconds=1)
    assert service.get_remaining_quota(identity) == 1000
    assert service.get_quota_reset_at(identity) == "2027-01-25T03:00:00+00:00"
    service.consume_quota(identity, consumed_units=100)
    clock[0] = datetime(2027, 1, 26, 3, tzinfo=timezone.utc)
    assert service.get_remaining_quota(identity) == 1000
    assert service.get_quota_reset_at(identity) == "2027-02-25T03:00:00+00:00"
    # Populate the active-plan cache just before expiry, then cross its boundary.
    clock[0] = datetime(2027, 2, 25, 3, tzinfo=timezone.utc) - timedelta(microseconds=1)
    assert service.resolve_identity(None, user.token).plan_code == "black_friday_3_meses"
    clock[0] += timedelta(microseconds=1)
    assert service.resolve_identity(None, user.token).plan_code is None


def test_monthly_black_and_existing_subscription_survive_campaign(campaign):
    service, clock = campaign
    existing = service.register_user(name="Existente", email="old@example.test", password="safe-password")
    service.activate_user_plan(user_id=existing.user_id, plan_code="essencial")
    original = service.resolve_identity(None, existing.token)
    black = service.register_user(name="Black", email="black@example.test", password="safe-password")
    intent = order(service, "essencial_black", black)
    PurchaseFlow(service).release(intent["id"], actor_kind="system", actor_user_id=None)
    clock[0] = datetime(2028, 1, 1, tzinfo=timezone.utc)
    assert service.resolve_identity(None, existing.token) == original
    assert service.resolve_identity(None, black.token).quota_limit == 600
    assert service.resolve_identity(None, black.token).plan_code == "essencial_black"


def test_http_cache_cannot_cross_campaign_boundary(campaign):
    service, clock = campaign
    clock[0] = END - timedelta(seconds=1)
    app.dependency_overrides[get_access_control_service] = lambda: service
    try:
        response = TestClient(app).get("/plans")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
    finally:
        app.dependency_overrides.clear()


def test_email_describes_single_payment_and_monthly_allowance(campaign):
    service, _ = campaign
    intent = order(service, "black_friday_3_meses")
    stored = service.read_checkout_intent_by_id(intent_id=intent["id"])
    email = render_purchase_email("customer_receipt", stored)
    assert "R$ 59,90 pelos 3 meses" in email["text"]
    assert "1.000 páginas por mês" in email["text"]
    assert "sem renovação automática" in email["text"]


def test_calendar_months_keep_original_anniversary_after_short_month():
    anchor = datetime(2027, 1, 31, 15, tzinfo=timezone.utc)
    assert add_months(anchor, 1) == datetime(2027, 2, 28, 15, tzinfo=timezone.utc)
    assert monthly_window(anchor, datetime(2027, 3, 30, 15, tzinfo=timezone.utc)) == (
        datetime(2027, 2, 28, 15, tzinfo=timezone.utc), datetime(2027, 3, 31, 15, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("code", OFFERS)
def test_paid_conversion_eligibility_is_preserved(campaign, code):
    service, _ = campaign
    user = service.register_user(name="Cliente", email="black@example.test", password="safe-password")
    service.activate_user_plan(user_id=user.user_id, plan_code=code)
    identity = service.resolve_identity(None, user.token)
    assert ConversionProQuota(service, ProConversionPolicy(enabled=True)).eligible(identity)


def test_stop_sales_preserves_existing_black_contracts(campaign, monkeypatch):
    service, _ = campaign
    user = service.register_user(name="Cliente", email="black@example.test", password="safe-password")
    service.activate_user_plan(user_id=user.user_id, plan_code="profissional_black")
    service.list_public_plans()
    monkeypatch.setenv("BLACK_FRIDAY_2026_ENABLED", "false")
    assert not set(OFFERS).intersection(plan["code"] for plan in service.list_public_plans())
    with pytest.raises(ValueError, match="25/11"):
        order(service, "essencial_black")
    assert service.resolve_identity(None, user.token).quota_limit == 1000
