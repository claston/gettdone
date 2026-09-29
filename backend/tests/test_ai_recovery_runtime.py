from __future__ import annotations

import pytest

from app.adapters.ai_recovery.runtime import build_ai_recovery_shadow_dispatcher


def test_runtime_builder_is_off_without_aws_resources() -> None:
    assert build_ai_recovery_shadow_dispatcher({}) is None


def test_runtime_builder_fails_fast_when_enabled_without_dedicated_resources() -> None:
    with pytest.raises(RuntimeError, match="AI_RECOVERY_S3_BUCKET"):
        build_ai_recovery_shadow_dispatcher({"AI_RECOVERY_MODE": "shadow"})
