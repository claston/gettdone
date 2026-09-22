import pytest

from app.application.ai_recovery.config import AIRecoveryConfig, AIRecoveryMode


def test_ai_recovery_config_is_off_by_default() -> None:
    config = AIRecoveryConfig.from_mapping({})

    assert config.mode == AIRecoveryMode.OFF
    assert config.model_id == "us.amazon.nova-2-lite-v1:0"
    assert config.region_name == "us-east-1"
    assert config.max_pages == 15
    assert config.max_input_bytes == 25 * 1024 * 1024
    assert config.timeout_seconds == 25
    assert config.max_output_tokens == 16000


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
        ({"AI_RECOVERY_MAX_INPUT_BYTES": "0"}, "AI_RECOVERY_MAX_INPUT_BYTES"),
        ({"AI_RECOVERY_MAX_INPUT_BYTES": str((25 * 1024 * 1024) + 1)}, "AI_RECOVERY_MAX_INPUT_BYTES"),
        ({"AI_RECOVERY_TIMEOUT_SECONDS": "0"}, "AI_RECOVERY_TIMEOUT_SECONDS"),
        ({"AI_RECOVERY_TIMEOUT_SECONDS": "31"}, "AI_RECOVERY_TIMEOUT_SECONDS"),
        ({"AI_RECOVERY_MAX_OUTPUT_TOKENS": "255"}, "AI_RECOVERY_MAX_OUTPUT_TOKENS"),
        ({"AI_RECOVERY_MAX_OUTPUT_TOKENS": "64001"}, "AI_RECOVERY_MAX_OUTPUT_TOKENS"),
        ({"AI_RECOVERY_MODEL_ID": ""}, "AI_RECOVERY_MODEL_ID"),
        ({"AI_RECOVERY_AWS_REGION": ""}, "AI_RECOVERY_AWS_REGION"),
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
            "AI_RECOVERY_MAX_INPUT_BYTES": "10485760",
            "AI_RECOVERY_TIMEOUT_SECONDS": "12",
            "AI_RECOVERY_MODEL_ID": "global.amazon.nova-2-lite-v1:0",
            "AI_RECOVERY_AWS_REGION": "us-west-2",
            "AI_RECOVERY_MAX_OUTPUT_TOKENS": "8000",
        }
    )

    assert config.mode == AIRecoveryMode.SHADOW
    assert config.max_pages == 8
    assert config.max_input_bytes == 10 * 1024 * 1024
    assert config.timeout_seconds == 12
    assert config.model_id == "global.amazon.nova-2-lite-v1:0"
    assert config.region_name == "us-west-2"
    assert config.max_output_tokens == 8000
