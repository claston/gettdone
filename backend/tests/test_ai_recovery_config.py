import pytest

from app.application.ai_recovery.config import AIRecoveryConfig, AIRecoveryMode


def test_ai_recovery_config_is_off_by_default() -> None:
    config = AIRecoveryConfig.from_mapping({})

    assert config.mode == AIRecoveryMode.OFF
    assert config.model_id == "us.amazon.nova-2-lite-v1:0"
    assert config.max_pages == 15
    assert config.timeout_seconds == 25


@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
def test_ai_recovery_config_accepts_supported_modes(mode: str) -> None:
    config = AIRecoveryConfig.from_mapping({"AI_RECOVERY_MODE": mode})

    assert config.mode.value == mode


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"AI_RECOVERY_MODE": "invalid"}, "AI_RECOVERY_MODE"),
        ({"AI_RECOVERY_MAX_PAGES": "0"}, "AI_RECOVERY_MAX_PAGES"),
        ({"AI_RECOVERY_MAX_PAGES": "16"}, "AI_RECOVERY_MAX_PAGES"),
        ({"AI_RECOVERY_TIMEOUT_SECONDS": "0"}, "AI_RECOVERY_TIMEOUT_SECONDS"),
        ({"AI_RECOVERY_TIMEOUT_SECONDS": "31"}, "AI_RECOVERY_TIMEOUT_SECONDS"),
        ({"AI_RECOVERY_MODEL_ID": ""}, "AI_RECOVERY_MODEL_ID"),
    ],
)
def test_ai_recovery_config_rejects_unsafe_values(environment: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AIRecoveryConfig.from_mapping(environment)


def test_ai_recovery_config_allows_lower_emergency_limits() -> None:
    config = AIRecoveryConfig.from_mapping(
        {
            "AI_RECOVERY_MODE": "shadow",
            "AI_RECOVERY_MAX_PAGES": "8",
            "AI_RECOVERY_TIMEOUT_SECONDS": "12",
            "AI_RECOVERY_MODEL_ID": "global.amazon.nova-2-lite-v1:0",
        }
    )

    assert config.mode == AIRecoveryMode.SHADOW
    assert config.max_pages == 8
    assert config.timeout_seconds == 12
    assert config.model_id == "global.amazon.nova-2-lite-v1:0"
