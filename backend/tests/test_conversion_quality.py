from types import SimpleNamespace

from app.application.conversion_quality import (
    QUALITY_RULE_VERSION,
    assess_conversion_quality,
    build_line_quality_issues,
    sanitize_failure_diagnostics,
)


def test_pdf_is_clean_only_with_specific_high_confidence_layout() -> None:
    assessment = assess_conversion_quality(
        status="Sucesso",
        conversion_type="pdf-ofx",
        transactions_count=12,
        layout_name="nubank_statement_ptbr",
        layout_confidence=0.97,
        selected_parser="inline",
        warning_count=0,
        balance_failed=0,
        parser_confidence_band="high",
        parser_coverage_rate=1.0,
    )

    assert assessment.status == "clean"
    assert assessment.score == 0.97
    assert assessment.rule_version == QUALITY_RULE_VERSION
    assert assessment.reason_codes == ()


def test_pdf_with_generic_or_low_confidence_layout_requires_review() -> None:
    generic = assess_conversion_quality(
        status="Sucesso",
        conversion_type="pdf-ofx",
        transactions_count=8,
        layout_name="generic_statement_ptbr",
        layout_confidence=0.99,
        selected_parser="tabular",
        warning_count=0,
        balance_failed=0,
    )
    low_confidence = assess_conversion_quality(
        status="Sucesso",
        conversion_type="pdf-ofx",
        transactions_count=8,
        layout_name="itau_statement_ptbr",
        layout_confidence=0.94,
        selected_parser="inline",
        warning_count=0,
        balance_failed=0,
    )

    assert generic.status == "review"
    assert "generic_layout" in generic.reason_codes
    assert low_confidence.status == "review"
    assert "layout_confidence_below_95" in low_confidence.reason_codes


def test_line_quality_issues_keep_location_but_not_financial_content() -> None:
    rows = [
        SimpleNamespace(
            warnings=["balance_consistency_failed", "amount_sign_inferred"],
            source_page=2,
            source_line=17,
            source_parser="tabular",
            description="PIX RECEBIDO DE MARIA",
            amount=1234.56,
        )
    ]

    issues = build_line_quality_issues(rows)

    assert issues == [
        {
            "scope": "transaction",
            "severity": "error",
            "issue_code": "balance_consistency_failed",
            "transaction_index": 0,
            "source_page": 2,
            "source_line": 17,
            "source_parser": "tabular",
        },
        {
            "scope": "transaction",
            "severity": "warning",
            "issue_code": "amount_sign_inferred",
            "transaction_index": 0,
            "source_page": 2,
            "source_line": 17,
            "source_parser": "tabular",
        },
    ]
    assert "MARIA" not in str(issues)
    assert "1234" not in str(issues)


def test_failure_diagnostics_drop_free_text_excerpt() -> None:
    sanitized = sanitize_failure_diagnostics(
        {
            "pdf_read_ok": True,
            "missing_signals": ["amount_pattern"],
            "inline_candidates": 0,
            "ocr_fallback_reason": "Conta 123, cliente Maria",
            "error_detail_excerpt": "Conta 123, cliente Maria",
            "unexpected": "must not persist",
        }
    )

    assert sanitized == {
        "pdf_read_ok": True,
        "missing_signals": ["amount_pattern"],
        "inline_candidates": 0,
    }
