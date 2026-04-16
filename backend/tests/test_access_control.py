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
