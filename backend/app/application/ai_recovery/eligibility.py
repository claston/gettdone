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

ELIGIBLE_RECOGNIZED_LAYOUT_ISSUES = frozenset({"balance_consistency_failed"})
ELIGIBLE_STATEMENT_TYPES = frozenset(
    {
        "conta_bloqueada_extrato",
        "conta_corrente",
        "conta_corrente_extrato",
        "conta_corrente_extrato_365_dias",
        "conta_corrente_extrato_autorizavel",
        "conta_corrente_extrato_completo",
        "conta_corrente_extrato_lancamentos",
        "conta_corrente_extrato_mensal",
        "conta_corrente_extrato_mensal_por_periodo",
        "conta_corrente_extrato_periodo_agrupado",
        "conta_corrente_extrato_por_periodo",
        "conta_corrente_extrato_texto",
        "conta_corrente_movimentacao",
        "conta_digital_extrato",
        "conta_digital_extrato_movimentacoes",
        "conta_digital_extrato_periodo",
        "conta_pagamento_extrato",
        "conta_pj_extrato",
        "conta_pj_extrato_bancario",
        "extrato_da_conta",
        "extrato_de_conta",
        "extrato_detalhado",
        "extrato_movimentacao",
        "extrato_movimentacoes",
        "poupanca_extrato",
    }
)
GENERIC_LAYOUT_NAMES = frozenset({"generic", "generic_pdf", "unknown"})
SHADOW_MIN_LAYOUT_CONFIDENCE = 0.70
ACTIVE_MIN_LAYOUT_CONFIDENCE = 0.95


class AIRecoveryEligibilityCase(str, Enum):
    CONTENT_EXTRACTION_FAILURE = "content_extraction_failure"
    RECOGNIZED_LAYOUT_DIVERGENCE = "recognized_layout_divergence"


class AIRecoveryEligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    PAGE_COUNT_UNKNOWN = "page_count_unknown"
    INVALID_PAGE_COUNT = "invalid_page_count"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    FILE_SIZE_UNKNOWN = "file_size_unknown"
    INVALID_FILE_SIZE = "invalid_file_size"
    FILE_SIZE_LIMIT_EXCEEDED = "file_size_limit_exceeded"
    DOCUMENT_HASH_MISSING = "document_hash_missing"
    INVALID_DOCUMENT_HASH = "invalid_document_hash"
    TRANSACTIONS_MISSING = "transactions_missing"
    LAYOUT_NOT_SPECIFIC = "layout_not_specific"
    LAYOUT_CONFIDENCE_UNKNOWN = "layout_confidence_unknown"
    INVALID_LAYOUT_CONFIDENCE = "invalid_layout_confidence"
    LAYOUT_CONFIDENCE_TOO_LOW = "layout_confidence_too_low"
    STATEMENT_TYPE_NOT_ALLOWLISTED = "statement_type_not_allowlisted"
    PARSER_UNKNOWN = "parser_unknown"
    ISSUE_NOT_ALLOWLISTED = "issue_not_allowlisted"
    SOURCE_EVIDENCE_UNAVAILABLE = "source_evidence_unavailable"
    FAILURE_NOT_ALLOWLISTED = "failure_not_allowlisted"


@dataclass(frozen=True, slots=True)
class AIRecoveryEligibilityContext:
    file_type: str
    page_count: int | None
    error_stage: str | None = None
    error_subcode: str | None = None
    case: AIRecoveryEligibilityCase | None = None
    file_size_bytes: int | None = None
    document_sha256: str | None = None
    transaction_count: int | None = None
    layout_name: str | None = None
    layout_confidence: float | None = None
    statement_type: str | None = None
    selected_parser: str | None = None
    issue_codes: tuple[str, ...] = ()
    source_evidence_available: bool = False


@dataclass(frozen=True, slots=True)
class AIRecoveryEligibilityDecision:
    eligible: bool
    reason: AIRecoveryEligibilityReason
    case: AIRecoveryEligibilityCase | None = None


def assess_ai_recovery_eligibility(
    *,
    config: AIRecoveryConfig,
    context: AIRecoveryEligibilityContext,
) -> AIRecoveryEligibilityDecision:
    eligibility_case = _resolve_case(context)
    if config.mode == AIRecoveryMode.OFF:
        return _ineligible(AIRecoveryEligibilityReason.DISABLED, eligibility_case)

    file_type = Path(str(context.file_type or "").strip().lower()).suffix.lstrip(".")
    if not file_type:
        file_type = str(context.file_type or "").strip().lower().lstrip(".")
    if file_type != "pdf":
        return _ineligible(AIRecoveryEligibilityReason.UNSUPPORTED_FILE_TYPE, eligibility_case)

    if context.page_count is None:
        return _ineligible(AIRecoveryEligibilityReason.PAGE_COUNT_UNKNOWN, eligibility_case)
    if context.page_count < 1:
        return _ineligible(AIRecoveryEligibilityReason.INVALID_PAGE_COUNT, eligibility_case)
    if context.page_count > config.max_pages:
        return _ineligible(AIRecoveryEligibilityReason.PAGE_LIMIT_EXCEEDED, eligibility_case)

    if eligibility_case == AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE:
        return _assess_recognized_layout_divergence(config=config, context=context)

    error_stage = str(context.error_stage or "").strip().lower()
    error_subcode = str(context.error_subcode or "").strip().lower()
    if error_stage not in {"parse", "ocr"} or error_subcode not in ELIGIBLE_FAILURE_SUBCODES:
        return _ineligible(AIRecoveryEligibilityReason.FAILURE_NOT_ALLOWLISTED, eligibility_case)

    return AIRecoveryEligibilityDecision(True, AIRecoveryEligibilityReason.ELIGIBLE, eligibility_case)


def _resolve_case(context: AIRecoveryEligibilityContext) -> AIRecoveryEligibilityCase:
    if context.case is not None:
        return context.case
    if context.error_stage or context.error_subcode:
        return AIRecoveryEligibilityCase.CONTENT_EXTRACTION_FAILURE
    return AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE


def _assess_recognized_layout_divergence(
    *,
    config: AIRecoveryConfig,
    context: AIRecoveryEligibilityContext,
) -> AIRecoveryEligibilityDecision:
    eligibility_case = AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE
    if context.file_size_bytes is None:
        return _ineligible(AIRecoveryEligibilityReason.FILE_SIZE_UNKNOWN, eligibility_case)
    if context.file_size_bytes < 1:
        return _ineligible(AIRecoveryEligibilityReason.INVALID_FILE_SIZE, eligibility_case)
    if context.file_size_bytes > config.max_input_bytes:
        return _ineligible(AIRecoveryEligibilityReason.FILE_SIZE_LIMIT_EXCEEDED, eligibility_case)

    document_sha256 = str(context.document_sha256 or "").strip().lower()
    if not document_sha256:
        return _ineligible(AIRecoveryEligibilityReason.DOCUMENT_HASH_MISSING, eligibility_case)
    if len(document_sha256) != 64 or any(character not in "0123456789abcdef" for character in document_sha256):
        return _ineligible(AIRecoveryEligibilityReason.INVALID_DOCUMENT_HASH, eligibility_case)

    if context.transaction_count is None or context.transaction_count < 1:
        return _ineligible(AIRecoveryEligibilityReason.TRANSACTIONS_MISSING, eligibility_case)

    layout_name = str(context.layout_name or "").strip().lower()
    if not layout_name or layout_name in GENERIC_LAYOUT_NAMES:
        return _ineligible(AIRecoveryEligibilityReason.LAYOUT_NOT_SPECIFIC, eligibility_case)

    if context.layout_confidence is None:
        return _ineligible(AIRecoveryEligibilityReason.LAYOUT_CONFIDENCE_UNKNOWN, eligibility_case)
    if not 0.0 <= context.layout_confidence <= 1.0:
        return _ineligible(AIRecoveryEligibilityReason.INVALID_LAYOUT_CONFIDENCE, eligibility_case)
    minimum_confidence = (
        ACTIVE_MIN_LAYOUT_CONFIDENCE if config.mode == AIRecoveryMode.ACTIVE else SHADOW_MIN_LAYOUT_CONFIDENCE
    )
    if context.layout_confidence < minimum_confidence:
        return _ineligible(AIRecoveryEligibilityReason.LAYOUT_CONFIDENCE_TOO_LOW, eligibility_case)

    statement_type = str(context.statement_type or "").strip().lower()
    if statement_type not in ELIGIBLE_STATEMENT_TYPES:
        return _ineligible(AIRecoveryEligibilityReason.STATEMENT_TYPE_NOT_ALLOWLISTED, eligibility_case)

    if not str(context.selected_parser or "").strip():
        return _ineligible(AIRecoveryEligibilityReason.PARSER_UNKNOWN, eligibility_case)

    issue_codes = {str(code or "").strip().lower() for code in context.issue_codes}
    if not issue_codes.intersection(ELIGIBLE_RECOGNIZED_LAYOUT_ISSUES):
        return _ineligible(AIRecoveryEligibilityReason.ISSUE_NOT_ALLOWLISTED, eligibility_case)

    if config.mode == AIRecoveryMode.ACTIVE and not context.source_evidence_available:
        return _ineligible(AIRecoveryEligibilityReason.SOURCE_EVIDENCE_UNAVAILABLE, eligibility_case)

    return AIRecoveryEligibilityDecision(True, AIRecoveryEligibilityReason.ELIGIBLE, eligibility_case)


def _ineligible(
    reason: AIRecoveryEligibilityReason,
    eligibility_case: AIRecoveryEligibilityCase | None,
) -> AIRecoveryEligibilityDecision:
    return AIRecoveryEligibilityDecision(False, reason, eligibility_case)
