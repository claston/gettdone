from app.application.normalization.pdf_parse_metrics import build_pdf_parse_metrics


def test_build_pdf_parse_metrics_preserves_contract_keys_and_values() -> None:
    canonical_quality_metrics = {
        "canonical_transactions_count": 2,
        "canonical_with_running_balance_count": 2,
        "canonical_with_external_reference_count": 1,
        "canonical_warning_count": 1,
        "canonical_balance_warning_count": 1,
        "canonical_warning_transactions_count": 1,
        "canonical_warning_types_count": 1,
        "canonical_warning_types": "balance_consistency_failed",
        "canonical_warning_types_list": "balance_consistency_failed",
        "canonical_running_balance_coverage_rate": 1.0,
        "canonical_external_reference_coverage_rate": 0.5,
        "canonical_warning_transaction_rate": 0.5,
        "canonical_source_parser_grouped_count": 0,
        "canonical_source_parser_inline_count": 0,
        "canonical_source_parser_tabular_count": 2,
        "canonical_source_parser_columnar_count": 0,
        "canonical_source_parser_types_count": 1,
        "canonical_source_parser_types": "tabular",
        "canonical_source_parser_types_list": "tabular",
    }

    metrics = build_pdf_parse_metrics(
        page_count=1,
        extracted_char_count=420,
        flattened_line_count=18,
        grouped_transactions_count=0,
        inline_candidates_count=0,
        inline_transactions_count=0,
        tabular_candidates_count=2,
        tabular_transactions_count=2,
        columnar_candidates_count=0,
        columnar_transactions_count=0,
        selected_parser="tabular",
        parser_selection_reason="tabular_rows_available_after_inline_empty",
        balance_consistency_checked=1,
        balance_consistency_failed=1,
        canonical_quality_metrics=canonical_quality_metrics,
    )

    assert metrics["page_count"] == 1
    assert metrics["extracted_char_count"] == 420
    assert metrics["flattened_line_count"] == 18
    assert metrics["grouped_transactions_count"] == 0
    assert metrics["selected_parser"] == "tabular"
    assert metrics["parser_selection_reason"] == "tabular_rows_available_after_inline_empty"
    assert metrics["tabular_candidates_count"] == 2
    assert metrics["tabular_transactions_count"] == 2
    assert metrics["columnar_candidates_count"] == 0
    assert metrics["columnar_transactions_count"] == 0
    assert metrics["balance_consistency_checked"] == 1
    assert metrics["balance_consistency_failed"] == 1
    assert metrics["canonical_transactions_count"] == 2
    assert metrics["canonical_warning_types_list"] == "balance_consistency_failed"
    assert metrics["canonical_source_parser_tabular_count"] == 2
    assert metrics["canonical_source_parser_types"] == "tabular"
