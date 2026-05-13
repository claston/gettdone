from app.application.models import CanonicalTransaction
from app.application.normalization.balance import annotate_balance_consistency


def test_annotate_balance_consistency_marks_only_inconsistent_rows() -> None:
    canonical_transactions = [
        CanonicalTransaction(
            date="2026-05-01",
            description="SALDO BASE",
            amount=0.0,
            type="inflow",
            running_balance=1000.0,
            source_parser="tabular",
        ),
        CanonicalTransaction(
            date="2026-05-02",
            description="PAGAMENTO",
            amount=-100.0,
            type="outflow",
            running_balance=900.0,
            source_parser="tabular",
        ),
        CanonicalTransaction(
            date="2026-05-03",
            description="TRANSFERENCIA",
            amount=-50.0,
            type="outflow",
            running_balance=870.0,
            source_parser="tabular",
        ),
    ]

    checked_count, failed_count = annotate_balance_consistency(canonical_transactions)

    assert checked_count == 2
    assert failed_count == 1
    assert "balance_consistency_failed" not in canonical_transactions[1].warnings
    assert "balance_consistency_failed" in canonical_transactions[2].warnings


def test_annotate_balance_consistency_ignores_rows_without_running_balance() -> None:
    canonical_transactions = [
        CanonicalTransaction(
            date="2026-05-01",
            description="PIX",
            amount=120.0,
            type="inflow",
            running_balance=None,
            source_parser="inline",
        ),
        CanonicalTransaction(
            date="2026-05-02",
            description="TED",
            amount=-20.0,
            type="outflow",
            running_balance=None,
            source_parser="inline",
        ),
    ]

    checked_count, failed_count = annotate_balance_consistency(canonical_transactions)

    assert checked_count == 0
    assert failed_count == 0
    assert canonical_transactions[0].warnings == []
    assert canonical_transactions[1].warnings == []
