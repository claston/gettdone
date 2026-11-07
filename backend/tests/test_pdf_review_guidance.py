"""Review severity follows evidence, never an issuer allowlist."""

from copy import deepcopy

import pytest

from app.application.pdf_review_guidance import build_pdf_review_guidance


def analysis(**metrics):
    return {
        "file_type": "pdf",
        "transactions_total": 3,
        "preview_transactions": [],
        "pdf_processing_metrics": {"export_recommendation": "review_recommended", **metrics},
    }


def shared_reference():
    return analysis(
        financial_controls_validated=1,
        balance_validation_status="validated",
        printed_balance_checks=6,
        statement_row_coverage_rate=1,
        export_recommendation_reason="low_confidence_band",
        canonical_warning_types_list=["manual_review_recommended", "shared_operation_reference"],
    )


@pytest.mark.parametrize("issuer", ["Asaas", "Itaú"])
def test_technical_confidence_is_not_a_financial_error(issuer):
    data = analysis(confidence_band="medium", export_recommendation_reason="medium_confidence_band")
    data["bank_name"] = issuer
    guide = build_pdf_review_guidance(data)
    assert guide["category"] == "validation_incomplete"
    assert "divergência" not in guide["title"].lower()
    assert "controles" in guide["message"]


@pytest.mark.parametrize("checks", [0, 5, 7])
def test_ocr_and_internal_controls_never_certify_global_balance(checks):
    guide = build_pdf_review_guidance(
        analysis(export_recommendation_reason="unverified_visual_recovery", printed_balance_checks=checks, balance_bounds_unavailable=1)
    )
    assert guide["category"] == "validation_incomplete"
    assert "OCR" in guide["message"]
    if checks:
        assert "controles impressos" in guide["message"]
        assert "saldo global" in guide["message"]


def test_successful_line_checks_are_recognized_with_limited_scope():
    guide = build_pdf_review_guidance(analysis(balance_consistency_checked=164, balance_consistency_failed=0))
    assert guide["category"] == "validation_incomplete"
    assert "164 verificações entre saldos" in guide["message"]
    assert "cobertura" in guide["message"]


def test_shared_reference_is_information_only_when_printed_controls_and_coverage_pass():
    original = shared_reference()
    original["preview_transactions"] = [{"amount": 20, "warning_types": ["shared_operation_reference"]}] * 3
    before = deepcopy(original)
    guide = build_pdf_review_guidance(original)
    assert guide["category"] == "information"
    assert "não comprova duplicidade" in guide["message"]
    assert "original" in guide["message"]
    assert original == before


@pytest.mark.parametrize(
    "patch",
    [
        {"financial_controls_validated": 0},
        {"statement_row_coverage_rate": 0.9},
        {"printed_balance_checks": 0},
        {"balance_bounds_unavailable": 1},
        {"partial_statement": 1},
        {"opening_balance_ambiguous": 1},
        {"ocr_unresolved_rows": 1},
        {"document_period_unavailable": 1},
        {"export_recommendation_reason": "unknown_future_reason"},
        {"canonical_warning_types_list": ["shared_operation_reference", "statement_blocks_not_contiguous"]},
        {"canonical_warning_types_list": ["shared_operation_reference", "ocr_amount_confidence_unconfirmed"]},
        {"canonical_warning_types_list": ["shared_operation_reference", "future_ambiguity"]},
    ],
)
def test_incomplete_or_unknown_evidence_cannot_downgrade_to_information(patch):
    data = shared_reference()
    data["pdf_processing_metrics"].update(patch)
    assert build_pdf_review_guidance(data)["category"] == "validation_incomplete"


@pytest.mark.parametrize(
    "patch",
    [
        {"balance_consistency_failed": 1},
        {"canonical_warning_types_list": ["shared_operation_reference", "financial_validation_failed"]},
        {"canonical_warning_types_list": ["shared_operation_reference", "printed_summary_balance_mismatch"]},
    ],
)
def test_real_discrepancy_wins_over_successful_controls(patch):
    data = shared_reference()
    data["pdf_processing_metrics"].update(patch)
    assert build_pdf_review_guidance(data)["category"] == "financial_discrepancy"


def test_row_discrepancy_and_statement_comparison_are_not_hidden_by_metrics():
    data = shared_reference()
    data["preview_transactions"] = [{"warning_types": ["balance_consistency_failed"]}]
    assert build_pdf_review_guidance(data)["category"] == "financial_discrepancy"
    data["preview_transactions"] = []
    data["statement_balance_check"] = {"difference": -1}
    assert build_pdf_review_guidance(data)["category"] == "financial_discrepancy"


def test_no_guidance_for_non_pdf_specialized_or_clean_documents():
    assert build_pdf_review_guidance({"file_type": "csv"}) is None
    assert build_pdf_review_guidance({**analysis(), "financial_document": {"document_type": "investment_statement"}}) is None
    assert build_pdf_review_guidance(analysis(export_recommendation="safe_to_export")) is None


def test_legacy_result_is_enriched_without_mutating_stored_financial_data():
    from app.application.balance_checks import enrich_legacy_conversion_result

    payload = {"analysis": shared_reference(), "processing_id": "synthetic"}
    original = deepcopy(payload)
    result = enrich_legacy_conversion_result(payload)
    assert result["analysis"]["review_guidance"]["category"] == "information"
    assert payload == original
    result["analysis"]["preview_transactions"] = [{"warning_types": ["balance_consistency_failed"]}]
    assert enrich_legacy_conversion_result(result)["analysis"]["review_guidance"]["category"] == "financial_discrepancy"
