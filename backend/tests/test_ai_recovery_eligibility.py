import pytest

from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.eligibility import (
    AIRecoveryEligibilityCase,
    AIRecoveryEligibilityContext,
    AIRecoveryEligibilityReason,
    assess_ai_recovery_eligibility,
)


def _config(mode: str = "active") -> AIRecoveryConfig:
    return AIRecoveryConfig.from_mapping({"AI_RECOVERY_MODE": mode})


def _case_1_context(**overrides: object) -> AIRecoveryEligibilityContext:
    values: dict[str, object] = {
        "file_type": "pdf",
        "page_count": 3,
        "case": AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE,
        "file_size_bytes": 1_000_000,
        "document_sha256": "a" * 64,
        "transaction_count": 10,
        "layout_name": "banco_inter_extrato_conta_corrente_saldo_transacao_v1",
        "layout_confidence": 0.70,
        "statement_type": "conta_corrente_extrato",
        "selected_parser": "pdf_tabular",
        "issue_codes": ("balance_consistency_failed",),
        "source_evidence_available": True,
    }
    values.update(overrides)
    return AIRecoveryEligibilityContext(**values)


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


def test_shadow_mode_accepts_recognized_statement_with_balance_divergence() -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config("shadow"),
        context=_case_1_context(source_evidence_available=False),
    )

    assert decision.eligible is True
    assert decision.reason == AIRecoveryEligibilityReason.ELIGIBLE
    assert decision.case == AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE


def test_active_mode_requires_higher_layout_confidence_and_source_evidence() -> None:
    low_confidence = assess_ai_recovery_eligibility(
        config=_config("active"),
        context=_case_1_context(layout_confidence=0.94),
    )
    missing_evidence = assess_ai_recovery_eligibility(
        config=_config("active"),
        context=_case_1_context(layout_confidence=0.95, source_evidence_available=False),
    )
    eligible = assess_ai_recovery_eligibility(
        config=_config("active"),
        context=_case_1_context(layout_confidence=0.95),
    )

    assert low_confidence.reason == AIRecoveryEligibilityReason.LAYOUT_CONFIDENCE_TOO_LOW
    assert missing_evidence.reason == AIRecoveryEligibilityReason.SOURCE_EVIDENCE_UNAVAILABLE
    assert eligible.eligible is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"file_size_bytes": 0}, AIRecoveryEligibilityReason.INVALID_FILE_SIZE),
        ({"file_size_bytes": (25 * 1024 * 1024) + 1}, AIRecoveryEligibilityReason.FILE_SIZE_LIMIT_EXCEEDED),
        ({"document_sha256": None}, AIRecoveryEligibilityReason.DOCUMENT_HASH_MISSING),
        ({"document_sha256": "not-a-sha256"}, AIRecoveryEligibilityReason.INVALID_DOCUMENT_HASH),
        ({"transaction_count": 0}, AIRecoveryEligibilityReason.TRANSACTIONS_MISSING),
        ({"layout_name": "generic_pdf"}, AIRecoveryEligibilityReason.LAYOUT_NOT_SPECIFIC),
        ({"layout_confidence": 0.69}, AIRecoveryEligibilityReason.LAYOUT_CONFIDENCE_TOO_LOW),
        ({"layout_confidence": 1.01}, AIRecoveryEligibilityReason.INVALID_LAYOUT_CONFIDENCE),
        ({"statement_type": "cartao_credito_fatura"}, AIRecoveryEligibilityReason.STATEMENT_TYPE_NOT_ALLOWLISTED),
        ({"statement_type": "comprovante_pagamento"}, AIRecoveryEligibilityReason.STATEMENT_TYPE_NOT_ALLOWLISTED),
        ({"selected_parser": ""}, AIRecoveryEligibilityReason.PARSER_UNKNOWN),
        ({"issue_codes": ("amount_sign_inferred",)}, AIRecoveryEligibilityReason.ISSUE_NOT_ALLOWLISTED),
    ],
)
def test_case_1_rejects_contexts_outside_the_narrow_contract(
    overrides: dict[str, object],
    reason: AIRecoveryEligibilityReason,
) -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config("shadow"),
        context=_case_1_context(**overrides),
    )

    assert decision.eligible is False
    assert decision.reason == reason
    assert decision.case == AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE


def test_legacy_content_failure_contract_remains_backward_compatible() -> None:
    decision = assess_ai_recovery_eligibility(
        config=_config("active"),
        context=AIRecoveryEligibilityContext(
            file_type="pdf",
            page_count=2,
            error_stage="parse",
            error_subcode="unsupported_table_layout",
        ),
    )

    assert decision.eligible is True
    assert decision.case == AIRecoveryEligibilityCase.CONTENT_EXTRACTION_FAILURE
