from datetime import datetime, timedelta, timezone

import pytest

from app.application.access_control import AccessControlService
from app.application.office_plan import OFFICE_PLAN, office_commercial


def test_private_versioned_plan_no_defaults_activation_retry_expiration_cancel(tmp_path):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="office-plan-test")
    user = access.register_user(name="Office", email="plan@example.test", password="long-password")
    catalog = access.list_public_plans()
    admin = dict(access=access, actor_kind="system", actor_user_id=None)
    config = dict(version=1, price_cents=None, quota_limit=None, max_pages_per_file=100, max_pages_per_file_ocr=6, batch_max_files=12)
    draft = office_commercial(**admin, operation_id="draft", action="configure", values=config)
    assert draft["active"] is False
    assert access.list_public_plans() == catalog
    activation = dict(user_id=user.user_id, expires_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    with pytest.raises(ValueError):
        office_commercial(**admin, operation_id="release-draft", action="activate", values=activation)
    office_commercial(
        **admin, operation_id="configured", action="configure", values={**config, "version": 2, "price_cents": 9000, "quota_limit": 10}
    )
    first = office_commercial(**admin, operation_id="release", action="activate", values=activation)
    assert office_commercial(**admin, operation_id="release", action="activate", values=activation) == first
    identity = access.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    assert identity.plan_code == OFFICE_PLAN and identity.quota_mode == "document_conversion"
    assert identity.quota_limit == 10 and identity.batch_conversion_enabled
    office_commercial(**admin, operation_id="cancel", action="cancel", values={"user_id": user.user_id})
    assert access.resolve_identity(anonymous_fingerprint=None, user_token=user.token).plan_code != OFFICE_PLAN
    office_commercial(**admin, operation_id="renew", action="activate", values=activation)
    with access.db.connect() as conn:
        access.db.execute(
            conn, "UPDATE user_plan_subscriptions SET ended_at = ? WHERE user_id = ?", ("2020-01-01T00:00:00+00:00", user.user_id)
        )
        conn.commit()
    access._invalidate_active_plan_cache(user.user_id)
    assert not access.resolve_identity(anonymous_fingerprint=None, user_token=user.token).batch_conversion_enabled
    assert access.list_public_plans() == catalog
