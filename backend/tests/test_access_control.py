from datetime import datetime, timedelta, timezone

from app.application.access_control import (
    ANONYMOUS_QUOTA_LIMIT,
    REGISTERED_QUOTA_LIMIT,
    AccessControlService,
)
from app.application.errors import FileTooLargeError, InvalidUserTokenError, QuotaExceededError


def test_anonymous_quota_blocks_4th_attempt(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    identity = service.resolve_identity(anonymous_fingerprint="anon-device-a", user_token=None)
    assert identity.identity_type == "anonymous"
    assert identity.quota_limit == ANONYMOUS_QUOTA_LIMIT

    assert service.get_remaining_quota(identity) == 3
    assert service.consume_quota(identity) == 2
    assert service.consume_quota(identity) == 1
    assert service.consume_quota(identity) == 0
    assert service.get_remaining_quota(identity) == 0

    try:
        service.consume_quota(identity)
        assert False, "Expected QuotaExceededError"
    except QuotaExceededError:
        assert True


def test_register_user_gets_10_quota_and_valid_token(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=registered.token)

    assert identity.identity_type == "user"
    assert identity.quota_limit == REGISTERED_QUOTA_LIMIT
    assert service.get_remaining_quota(identity) == 10


def test_upload_larger_than_2mb_is_rejected(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    raw = b"a" * ((2 * 1024 * 1024) + 1)
    try:
        service.assert_upload_size(raw)
        assert False, "Expected FileTooLargeError"
    except FileTooLargeError:
        assert True


def test_invalid_token_is_rejected(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    try:
        service.resolve_identity(anonymous_fingerprint=None, user_token="invalid.token")
        assert False, "Expected InvalidUserTokenError"
    except InvalidUserTokenError:
        assert True


def test_custom_anonymous_quota_limit_is_applied(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        anonymous_quota_limit=99,
    )

    identity = service.resolve_identity(anonymous_fingerprint="anon-device-b", user_token=None)
    assert identity.identity_type == "anonymous"
    assert identity.quota_limit == 99
    assert service.get_remaining_quota(identity) == 99


def test_anonymous_quota_resets_after_week_window(tmp_path) -> None:
    now_box = [datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)]
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        now_provider=lambda: now_box[0],
    )
    identity = service.resolve_identity(anonymous_fingerprint="anon-device-weekly", user_token=None)

    assert service.consume_quota(identity) == 2
    assert service.consume_quota(identity) == 1
    assert service.consume_quota(identity) == 0
    try:
        service.consume_quota(identity)
        assert False, "Expected QuotaExceededError"
    except QuotaExceededError:
        assert True

    now_box[0] = now_box[0] + timedelta(days=8)
    assert service.get_remaining_quota(identity) == 3
    assert service.consume_quota(identity) == 2


def test_google_user_is_created_and_reused_by_provider_id(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    first = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-1",
        email="erica@example.com",
        name="Erica",
    )
    second = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-1",
        email="erica@example.com",
        name="Erica Souza",
    )

    assert first.user_id == second.user_id
    assert second.name == "Erica Souza"
    assert second.token


def test_google_oauth_state_can_be_consumed_once(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    state, verifier = service.create_google_oauth_state(next_path="/client-area.html", ttl_seconds=600)
    consumed = service.consume_google_oauth_state(state=state)
    consumed_again = service.consume_google_oauth_state(state=state)

    assert consumed is not None
    assert consumed["state"] == state
    assert consumed["code_verifier"] == verifier
    assert consumed["next_path"] == "/client-area.html"
    assert consumed_again is None
