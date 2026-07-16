from app.application.models import CanonicalTransaction


def build_canonical_quality_metrics(canonical_transactions: list[CanonicalTransaction]) -> dict[str, int | float | str]:
    warning_count = sum(len(item.warnings) for item in canonical_transactions)
    balance_warning_count = sum(1 for item in canonical_transactions if "balance_consistency_failed" in item.warnings)
    with_running_balance_count = sum(1 for item in canonical_transactions if item.running_balance is not None)
    with_external_reference_count = sum(1 for item in canonical_transactions if item.external_reference_id)
    total_count = len(canonical_transactions)
    running_balance_coverage_rate = (
        round(with_running_balance_count / total_count, 4) if total_count > 0 else 0.0
    )
    external_reference_coverage_rate = (
        round(with_external_reference_count / total_count, 4) if total_count > 0 else 0.0
    )
    warning_transaction_rate = (
        round(sum(1 for item in canonical_transactions if item.warnings) / total_count, 4) if total_count > 0 else 0.0
    )
    warning_types = sorted(
        {
            warning
            for item in canonical_transactions
            for warning in item.warnings
        }
    )
    parser_counts = {
        "grouped": sum(1 for item in canonical_transactions if item.source_parser == "grouped"),
        "inline": sum(1 for item in canonical_transactions if item.source_parser == "inline"),
        "tabular": sum(1 for item in canonical_transactions if item.source_parser == "tabular"),
        "columnar": sum(1 for item in canonical_transactions if item.source_parser == "columnar"),
        "multiline": sum(1 for item in canonical_transactions if item.source_parser == "multiline"),
    }
    parser_types = sorted(
        {
            str(item.source_parser).strip()
            for item in canonical_transactions
            if str(item.source_parser or "").strip()
        }
    )
    return {
        "canonical_transactions_count": total_count,
        "canonical_with_running_balance_count": with_running_balance_count,
        "canonical_with_external_reference_count": with_external_reference_count,
        "canonical_warning_count": warning_count,
        "canonical_balance_warning_count": balance_warning_count,
        "canonical_warning_transactions_count": sum(1 for item in canonical_transactions if item.warnings),
        "canonical_warning_types_count": len(
            {
                warning
                for item in canonical_transactions
                for warning in item.warnings
            }
        ),
        "canonical_warning_types": ",".join(warning_types),
        "canonical_warning_types_list": "|".join(warning_types),
        "canonical_running_balance_coverage_rate": running_balance_coverage_rate,
        "canonical_external_reference_coverage_rate": external_reference_coverage_rate,
        "canonical_warning_transaction_rate": warning_transaction_rate,
        "canonical_source_parser_grouped_count": parser_counts["grouped"],
        "canonical_source_parser_inline_count": parser_counts["inline"],
        "canonical_source_parser_tabular_count": parser_counts["tabular"],
        "canonical_source_parser_columnar_count": parser_counts["columnar"],
        "canonical_source_parser_multiline_count": parser_counts["multiline"],
        "canonical_source_parser_types_count": len(parser_types),
        "canonical_source_parser_types": ",".join(parser_types),
        "canonical_source_parser_types_list": "|".join(parser_types),
    }
