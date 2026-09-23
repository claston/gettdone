from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.application.access_control import IdentityContext, RegisteredUser
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy


@dataclass
class FakeAccessControl:
    user: RegisteredUser

    def get_user_by_id(self, user_id: str) -> RegisteredUser:
        assert user_id == self.user.user_id
        return self.user


def _user(email: str = "a@a.com.br", *, user_id: str = "usr_canary") -> RegisteredUser:
    return RegisteredUser(
        user_id=user_id,
        email=email,
        name="Canary",
        token="user-token",
    )


def test_rollout_policy_normalizes_email_allowlist_and_allows_only_registered_user() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": " A@A.COM.BR, second@example.com "}
    )
    access_control = FakeAccessControl(user=_user())

    assert policy.enabled is True
    assert policy.allows(
        identity=IdentityContext(identity_type="user", identity_id="usr_canary", quota_limit=10),
        access_control_service=access_control,
    )
    assert not policy.allows(
        identity=IdentityContext(identity_type="anonymous", identity_id="anon_123", quota_limit=3),
        access_control_service=access_control,
    )


def test_rollout_policy_rejects_non_allowlisted_user() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br"}
    )

    assert not policy.allows(
        identity=IdentityContext(identity_type="user", identity_id="usr_canary", quota_limit=10),
        access_control_service=FakeAccessControl(user=_user("other@example.com")),
    )


def test_rollout_policy_rejects_invalid_configured_email() -> None:
    with pytest.raises(ValueError, match="valid email"):
        AsyncConversionRolloutPolicy.from_mapping(
            {"CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "not-an-email"}
        )


def test_rollout_policy_keeps_allowlist_enabled_when_percentage_flag_is_off() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {
            "CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br",
            "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "false",
            "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "30",
        }
    )

    assert policy.allows(
        identity=IdentityContext(identity_type="user", identity_id="usr_canary", quota_limit=10),
        access_control_service=FakeAccessControl(user=_user()),
    )


def test_rollout_policy_routes_a_stable_percentage_of_registered_users() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {
            "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "true",
            "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "30",
        }
    )
    access_control = FakeAccessControl(user=_user("other@example.com"))

    decisions = [
        policy.allows(
            identity=IdentityContext(identity_type="user", identity_id=f"usr_{index}", quota_limit=10),
            access_control_service=access_control,
        )
        for index in range(1000)
    ]

    assert 250 <= sum(decisions) <= 350
    assert decisions == [
        policy.allows(
            identity=IdentityContext(identity_type="user", identity_id=f"usr_{index}", quota_limit=10),
            access_control_service=access_control,
        )
        for index in range(1000)
    ]


def test_rollout_policy_limits_percentage_users_but_keeps_allowlisted_batch_limit() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {
            "CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST": "a@a.com.br",
            "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "true",
            "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "100",
        }
    )

    assert policy.batch_max_files(
        identity=IdentityContext(identity_type="user", identity_id="usr_canary", quota_limit=10),
        access_control_service=FakeAccessControl(user=_user()),
        configured_max=12,
    ) == 12
    assert policy.batch_max_files(
        identity=IdentityContext(identity_type="user", identity_id="usr_percentage", quota_limit=10),
        access_control_service=FakeAccessControl(user=_user("other@example.com", user_id="usr_percentage")),
        configured_max=12,
    ) == 1


def test_rollout_policy_percentage_flag_can_disable_percentage_traffic() -> None:
    policy = AsyncConversionRolloutPolicy.from_mapping(
        {
            "CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED": "false",
            "CONVERSION_ASYNC_ROLLOUT_PERCENTAGE": "30",
        }
    )

    assert policy.enabled is False
    assert not policy.allows(
        identity=IdentityContext(identity_type="user", identity_id="usr_percentage", quota_limit=10),
        access_control_service=FakeAccessControl(user=_user("other@example.com")),
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED", "sometimes"),
        ("CONVERSION_ASYNC_ROLLOUT_PERCENTAGE", "101"),
        ("CONVERSION_ASYNC_ROLLOUT_PERCENTAGE", "thirty"),
    ],
)
def test_rollout_policy_rejects_invalid_percentage_configuration(name: str, value: str) -> None:
    with pytest.raises(ValueError, match="CONVERSION_ASYNC"):
        AsyncConversionRolloutPolicy.from_mapping({name: value})
