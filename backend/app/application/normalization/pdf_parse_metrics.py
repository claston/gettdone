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
    layout_used_fallback: bool,
    balance_consistency_checked: int,
    balance_consistency_failed: int,
    canonical_quality_metrics: dict[str, int | float | str],
    invalid_date_candidates_skipped: int = 0,
) -> dict[str, int | float | str]:
    confidence_band = _resolve_confidence_band(
        selected_parser=selected_parser,
        parser_selection_reason=parser_selection_reason,
        grouped_transactions_count=grouped_transactions_count,
        inline_candidates_count=inline_candidates_count,
        inline_transactions_count=inline_transactions_count,
        tabular_candidates_count=tabular_candidates_count,
        tabular_transactions_count=tabular_transactions_count,
        columnar_candidates_count=columnar_candidates_count,
        columnar_transactions_count=columnar_transactions_count,
        layout_used_fallback=layout_used_fallback,
        balance_consistency_failed=balance_consistency_failed,
        canonical_warning_count=int(canonical_quality_metrics["canonical_warning_count"]),
    )
    export_recommendation, export_recommendation_reason = _resolve_export_recommendation(
        confidence_band=confidence_band
    )
    return {
        "page_count": page_count,
        "extracted_char_count": extracted_char_count,
        "flattened_line_count": flattened_line_count,
        "invalid_date_candidates_skipped": invalid_date_candidates_skipped,
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
        "export_recommendation": export_recommendation,
        "export_recommendation_reason": export_recommendation_reason,
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
        "canonical_source_parser_multiline_count": canonical_quality_metrics.get(
            "canonical_source_parser_multiline_count",
            0,
        ),
        "canonical_source_parser_types_count": canonical_quality_metrics["canonical_source_parser_types_count"],
        "canonical_source_parser_types": canonical_quality_metrics["canonical_source_parser_types"],
        "canonical_source_parser_types_list": canonical_quality_metrics["canonical_source_parser_types_list"],
    }


def _resolve_confidence_band(
    *,
    selected_parser: str,
    parser_selection_reason: str,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
    tabular_candidates_count: int,
    tabular_transactions_count: int,
    columnar_candidates_count: int,
    columnar_transactions_count: int,
    layout_used_fallback: bool,
    balance_consistency_failed: int,
    canonical_warning_count: int,
) -> str:
    if balance_consistency_failed > 0 or canonical_warning_count > 0:
        return "low"
    coverage = _resolve_selected_parser_coverage(
        selected_parser=selected_parser,
        grouped_transactions_count=grouped_transactions_count,
        inline_candidates_count=inline_candidates_count,
        inline_transactions_count=inline_transactions_count,
        tabular_candidates_count=tabular_candidates_count,
        tabular_transactions_count=tabular_transactions_count,
        columnar_candidates_count=columnar_candidates_count,
        columnar_transactions_count=columnar_transactions_count,
    )
    if coverage is not None:
        if coverage < 0.5:
            return "low"
        if coverage < 0.85:
            return "medium"
    if layout_used_fallback:
        return "medium"
    if selected_parser != "inline":
        return "medium"
    if "conflict" in parser_selection_reason:
        return "medium"
    return "high"


def _resolve_selected_parser_coverage(
    *,
    selected_parser: str,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
    tabular_candidates_count: int,
    tabular_transactions_count: int,
    columnar_candidates_count: int,
    columnar_transactions_count: int,
) -> float | None:
    if selected_parser == "grouped":
        return 1.0 if grouped_transactions_count > 0 else None
    if selected_parser == "inline":
        return _safe_coverage(inline_transactions_count, inline_candidates_count)
    if selected_parser == "tabular":
        return _safe_coverage(tabular_transactions_count, tabular_candidates_count)
    if selected_parser == "columnar":
        return _safe_coverage(columnar_transactions_count, columnar_candidates_count)
    return None


def _safe_coverage(transactions_count: int, candidates_count: int) -> float | None:
    if candidates_count <= 0:
        return None
    if transactions_count <= 0:
        return 0.0
    return transactions_count / candidates_count


def _resolve_export_recommendation(*, confidence_band: str) -> tuple[str, str]:
    if confidence_band == "high":
        return "safe_to_export", "high_confidence_band"
    if confidence_band == "medium":
        return "review_recommended", "medium_confidence_band"
    return "review_recommended", "low_confidence_band"
