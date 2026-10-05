from __future__ import annotations

import pytest

from app.adapters.ai_recovery.runtime import build_ai_recovery_shadow_dispatcher


def test_runtime_builder_is_off_without_aws_resources() -> None:
    assert build_ai_recovery_shadow_dispatcher({}) is None


def test_runtime_builder_fails_fast_when_enabled_without_dedicated_resources() -> None:
    with pytest.raises(RuntimeError, match="AI_RECOVERY_S3_BUCKET"):
        build_ai_recovery_shadow_dispatcher({"AI_RECOVERY_MODE": "shadow"})


def test_runtime_builder_does_not_require_queue_when_bedrock_is_disabled() -> None:
    dispatcher = build_ai_recovery_shadow_dispatcher(
        {
            "AI_RECOVERY_MODE": "shadow",
            "AI_RECOVERY_S3_BUCKET": "private-ai-recovery",
        }
    )

    assert dispatcher is not None
    assert dispatcher.queue_publisher is None


def test_runtime_builder_requires_queue_for_explicit_bedrock_opt_in() -> None:
    with pytest.raises(RuntimeError, match="AI_RECOVERY_SQS_QUEUE_URL"):
        build_ai_recovery_shadow_dispatcher(
            {
                "AI_RECOVERY_MODE": "shadow",
                "AI_RECOVERY_BEDROCK_ENABLED": "true",
                "AI_RECOVERY_S3_BUCKET": "private-ai-recovery",
            }
        )
