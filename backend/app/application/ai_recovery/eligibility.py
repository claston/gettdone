from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.application.ai_recovery.config import AIRecoveryConfig, AIRecoveryMode

ELIGIBLE_FAILURE_SUBCODES = frozenset(
    {
        "unsupported_table_layout",
        "no_transaction_row_pattern",
        "insufficient_text",
        "ocr_no_usable_transactions",
    }
)


class AIRecoveryEligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    PAGE_COUNT_UNKNOWN = "page_count_unknown"
    INVALID_PAGE_COUNT = "invalid_page_count"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    FAILURE_NOT_ALLOWLISTED = "failure_not_allowlisted"


@dataclass(frozen=True, slots=True)
class AIRecoveryEligibilityContext:
    file_type: str
    page_count: int | None
    error_stage: str | None
    error_subcode: str | None


@dataclass(frozen=True, slots=True)
class AIRecoveryEligibilityDecision:
    eligible: bool
    reason: AIRecoveryEligibilityReason


def assess_ai_recovery_eligibility(
    *,
    config: AIRecoveryConfig,
    context: AIRecoveryEligibilityContext,
) -> AIRecoveryEligibilityDecision:
    if config.mode == AIRecoveryMode.OFF:
        return _ineligible(AIRecoveryEligibilityReason.DISABLED)

    file_type = Path(str(context.file_type or "").strip().lower()).suffix.lstrip(".")
    if not file_type:
        file_type = str(context.file_type or "").strip().lower().lstrip(".")
    if file_type != "pdf":
        return _ineligible(AIRecoveryEligibilityReason.UNSUPPORTED_FILE_TYPE)

    if context.page_count is None:
        return _ineligible(AIRecoveryEligibilityReason.PAGE_COUNT_UNKNOWN)
    if context.page_count < 1:
        return _ineligible(AIRecoveryEligibilityReason.INVALID_PAGE_COUNT)
    if context.page_count > config.max_pages:
        return _ineligible(AIRecoveryEligibilityReason.PAGE_LIMIT_EXCEEDED)

    error_stage = str(context.error_stage or "").strip().lower()
    error_subcode = str(context.error_subcode or "").strip().lower()
    if error_stage not in {"parse", "ocr"} or error_subcode not in ELIGIBLE_FAILURE_SUBCODES:
        return _ineligible(AIRecoveryEligibilityReason.FAILURE_NOT_ALLOWLISTED)

    return AIRecoveryEligibilityDecision(True, AIRecoveryEligibilityReason.ELIGIBLE)


def _ineligible(reason: AIRecoveryEligibilityReason) -> AIRecoveryEligibilityDecision:
    return AIRecoveryEligibilityDecision(False, reason)
