from app.application.models import CanonicalTransaction
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics


def test_build_canonical_quality_metrics_aggregates_warnings_balance_and_source_parsers() -> None:
    canonical_transactions = [
        CanonicalTransaction(
            date="2026-05-01",
            description="PIX RECEBIDO",
            amount=1000.0,
            type="inflow",
            source_parser="tabular",
            running_balance=2000.0,
            external_reference_id="doc-1",
        ),
        CanonicalTransaction(
            date="2026-05-02",
            description="TARIFA",
            amount=-20.0,
            type="outflow",
            source_parser="tabular",
            running_balance=1980.0,
            warnings=["balance_consistency_failed"],
        ),
        CanonicalTransaction(
            date="2026-05-03",
            description="TED",
            amount=400.0,
            type="inflow",
            source_parser="inline",
            warnings=["missing_reference"],
        ),
    ]

    metrics = build_canonical_quality_metrics(canonical_transactions)

    assert metrics["canonical_transactions_count"] == 3
    assert metrics["canonical_warning_count"] == 2
    assert metrics["canonical_balance_warning_count"] == 1
    assert metrics["canonical_warning_transactions_count"] == 2
    assert metrics["canonical_warning_types_count"] == 2
    assert metrics["canonical_warning_types"] == "balance_consistency_failed,missing_reference"
    assert metrics["canonical_warning_types_list"] == "balance_consistency_failed|missing_reference"
    assert metrics["canonical_with_running_balance_count"] == 2
    assert metrics["canonical_with_external_reference_count"] == 1
    assert metrics["canonical_running_balance_coverage_rate"] == 0.6667
    assert metrics["canonical_external_reference_coverage_rate"] == 0.3333
    assert metrics["canonical_warning_transaction_rate"] == 0.6667
    assert metrics["canonical_source_parser_grouped_count"] == 0
    assert metrics["canonical_source_parser_inline_count"] == 1
    assert metrics["canonical_source_parser_tabular_count"] == 2
    assert metrics["canonical_source_parser_columnar_count"] == 0
    assert metrics["canonical_source_parser_types_count"] == 2
    assert metrics["canonical_source_parser_types"] == "inline,tabular"
    assert metrics["canonical_source_parser_types_list"] == "inline|tabular"
