import pytest
from fastapi.testclient import TestClient

from app.application import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


@pytest.mark.parametrize("state", ["free", "pending", "active", "exhausted", "ended"])
def test_auth_me_exposes_paid_identity_only_for_active_subscription(tmp_path, state):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test-secret", active_plan_cache_ttl_seconds=0)
    user = service.register_user(name="Cliente", email="client@example.com", password="strong-pass")
    if state == "pending":
        service.create_checkout_intent(user_id=user.user_id, plan_code="essencial", customer_name="Cliente",
                                      customer_email=user.email, customer_whatsapp="synthetic-contact")
    if state in {"active", "exhausted", "ended"}:
        service.activate_user_plan(user_id=user.user_id, plan_code="essencial")
    if state == "exhausted":
        identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
        service.consume_quota(identity, consumed_units=identity.quota_limit)
    if state == "ended":
        with service._connect() as conn:
            service._execute(conn, "UPDATE user_plan_subscriptions SET status = 'ended', ended_at = ? WHERE user_id = ?",
                             (service.now_provider().isoformat(), user.user_id))
            conn.commit()
    app.dependency_overrides[get_access_control_service] = lambda: service
    try:
        response = TestClient(app).get("/auth/me", headers={"Authorization": f"Bearer {user.token}"})
        assert response.status_code == 200
        payload = response.json()
        if state in {"active", "exhausted"}:
            assert payload["quota_mode"] == "pages"
            assert payload["plan_code"] == "essencial"
            if state == "exhausted":
                assert payload["quota_remaining"] == 0
        else:
            assert payload["quota_mode"] == "conversion"
            assert payload["plan_code"] is None
    finally:
        app.dependency_overrides.pop(get_access_control_service, None)
        service.close()
