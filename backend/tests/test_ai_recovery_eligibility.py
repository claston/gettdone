import pytest

from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.eligibility import (
    AIRecoveryEligibilityContext,
    AIRecoveryEligibilityReason,
    assess_ai_recovery_eligibility,
)


def _config(mode: str = "active") -> AIRecoveryConfig:
    return AIRecoveryConfig.from_mapping({"AI_RECOVERY_MODE": mode})


@pytest.mark.parametrize(
    "subcode",
    [
        "unsupported_table_layout",
        "no_transaction_row_pattern",
        "insufficient_text",
        "ocr_no_usable_transactions",
    ],
)
def test_ai_recovery_is_eligible_only_for_allowlisted_content_failures(subcode: str) -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config(),
        context=AIRecoveryEligibilityContext(
            file_type="pdf",
            page_count=15,
            error_stage="parse",
            error_subcode=subcode,
        ),
    )

    assert decision.eligible is True
    assert decision.reason == AIRecoveryEligibilityReason.ELIGIBLE


@pytest.mark.parametrize(
    ("stage", "subcode"),
    [
        ("native_pdf_read", "password_protected_pdf"),
        ("native_pdf_read", "corrupted_pdf"),
        ("ocr", "ocr_timeout"),
        ("ocr", "ocr_dependency_missing"),
        ("processing", "processing_failed"),
        ("upload_validation", "pdf_page_limit_exceeded"),
    ],
)
def test_ai_recovery_rejects_operational_and_unsafe_failures(stage: str, subcode: str) -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config(),
        context=AIRecoveryEligibilityContext(
            file_type="pdf",
            page_count=2,
            error_stage=stage,
            error_subcode=subcode,
        ),
    )

    assert decision.eligible is False
    assert decision.reason == AIRecoveryEligibilityReason.FAILURE_NOT_ALLOWLISTED


@pytest.mark.parametrize(
    ("mode", "file_type", "page_count", "reason"),
    [
        ("off", "pdf", 2, AIRecoveryEligibilityReason.DISABLED),
        ("active", "csv", 2, AIRecoveryEligibilityReason.UNSUPPORTED_FILE_TYPE),
        ("active", "pdf", None, AIRecoveryEligibilityReason.PAGE_COUNT_UNKNOWN),
        ("active", "pdf", 0, AIRecoveryEligibilityReason.INVALID_PAGE_COUNT),
        ("active", "pdf", 16, AIRecoveryEligibilityReason.PAGE_LIMIT_EXCEEDED),
    ],
)
def test_ai_recovery_applies_global_safety_gates(
    mode: str,
    file_type: str,
    page_count: int | None,
    reason: AIRecoveryEligibilityReason,
) -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config(mode),
        context=AIRecoveryEligibilityContext(
            file_type=file_type,
            page_count=page_count,
            error_stage="parse",
            error_subcode="unsupported_table_layout",
        ),
    )

    assert decision.eligible is False
    assert decision.reason == reason


def test_shadow_mode_uses_the_same_eligibility_policy() -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config("shadow"),
        context=AIRecoveryEligibilityContext(
            file_type=".PDF",
            page_count=3,
            error_stage="parse",
            error_subcode="no_transaction_row_pattern",
        ),
    )

    assert decision.eligible is True
