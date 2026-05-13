def build_pdf_parse_metrics(
    *,
    page_count: int,
    extracted_char_count: int,
    flattened_line_count: int,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
    tabular_candidates_count: int,
    tabular_transactions_count: int,
    columnar_candidates_count: int,
    columnar_transactions_count: int,
    selected_parser: str,
    parser_selection_reason: str,
    inline_decision: str,
    tabular_decision: str,
    columnar_decision: str,
    balance_consistency_checked: int,
    balance_consistency_failed: int,
    canonical_quality_metrics: dict[str, int | float | str],
) -> dict[str, int | float | str]:
    confidence_band = _resolve_confidence_band(
        selected_parser=selected_parser,
        parser_selection_reason=parser_selection_reason,
        balance_consistency_failed=balance_consistency_failed,
        canonical_warning_count=int(canonical_quality_metrics["canonical_warning_count"]),
    )
    return {
        "page_count": page_count,
        "extracted_char_count": extracted_char_count,
        "flattened_line_count": flattened_line_count,
        "grouped_transactions_count": grouped_transactions_count,
        "inline_candidates_count": inline_candidates_count,
        "inline_transactions_count": inline_transactions_count,
        "tabular_candidates_count": tabular_candidates_count,
        "tabular_transactions_count": tabular_transactions_count,
        "columnar_candidates_count": columnar_candidates_count,
        "columnar_transactions_count": columnar_transactions_count,
        "selected_parser": selected_parser,
        "parser_selection_reason": parser_selection_reason,
        "inline_decision": inline_decision,
        "tabular_decision": tabular_decision,
        "columnar_decision": columnar_decision,
        "confidence_band": confidence_band,
        "balance_consistency_checked": balance_consistency_checked,
        "balance_consistency_failed": balance_consistency_failed,
        "canonical_transactions_count": canonical_quality_metrics["canonical_transactions_count"],
        "canonical_with_running_balance_count": canonical_quality_metrics["canonical_with_running_balance_count"],
        "canonical_with_external_reference_count": canonical_quality_metrics["canonical_with_external_reference_count"],
        "canonical_warning_count": canonical_quality_metrics["canonical_warning_count"],
        "canonical_balance_warning_count": canonical_quality_metrics["canonical_balance_warning_count"],
        "canonical_warning_transactions_count": canonical_quality_metrics["canonical_warning_transactions_count"],
        "canonical_warning_types_count": canonical_quality_metrics["canonical_warning_types_count"],
        "canonical_warning_types": canonical_quality_metrics["canonical_warning_types"],
        "canonical_warning_types_list": canonical_quality_metrics["canonical_warning_types_list"],
        "canonical_running_balance_coverage_rate": canonical_quality_metrics[
            "canonical_running_balance_coverage_rate"
        ],
        "canonical_external_reference_coverage_rate": canonical_quality_metrics[
            "canonical_external_reference_coverage_rate"
        ],
        "canonical_warning_transaction_rate": canonical_quality_metrics["canonical_warning_transaction_rate"],
        "canonical_source_parser_grouped_count": canonical_quality_metrics["canonical_source_parser_grouped_count"],
        "canonical_source_parser_inline_count": canonical_quality_metrics["canonical_source_parser_inline_count"],
        "canonical_source_parser_tabular_count": canonical_quality_metrics["canonical_source_parser_tabular_count"],
        "canonical_source_parser_columnar_count": canonical_quality_metrics["canonical_source_parser_columnar_count"],
        "canonical_source_parser_types_count": canonical_quality_metrics["canonical_source_parser_types_count"],
        "canonical_source_parser_types": canonical_quality_metrics["canonical_source_parser_types"],
        "canonical_source_parser_types_list": canonical_quality_metrics["canonical_source_parser_types_list"],
    }


def _resolve_confidence_band(
    *,
    selected_parser: str,
    parser_selection_reason: str,
    balance_consistency_failed: int,
    canonical_warning_count: int,
) -> str:
    if balance_consistency_failed > 0 or canonical_warning_count > 0:
        return "low"
    if selected_parser != "inline":
        return "medium"
    if "conflict" in parser_selection_reason:
        return "medium"
    return "high"
