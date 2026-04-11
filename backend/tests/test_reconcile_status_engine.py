from app.application.ledger_match_models import Ledger1to1Match, LedgerMatchResult
from app.application.models import NormalizedTransaction
from app.application.reconcile_status_engine import classify_reconciliation_rows


def test_classify_reconciliation_rows_marks_matched_rows_as_conciliated() -> None:
    bank_rows = [
        NormalizedTransaction(date="2026-04-01", description="PAGAMENTO ALFA", amount=-100.0, type="outflow"),
    ]
    sheet_rows = [
        NormalizedTransaction(date="2026-04-01", description="PAGAMENTO ALFA", amount=-100.0, type="outflow"),
    ]
    match_result = LedgerMatchResult(
        matches=[
            Ledger1to1Match(
                bank_index=0,
                sheet_index=0,
                date="2026-04-01",
                amount=-100.0,
                match_rule="exact",
                reason="matched_exact_value_and_date",
            )
        ],
        exact_matches_count=1,
        date_tolerance_matches_count=0,
        description_similarity_matches_count=0,
        total_matches_count=1,
        bank_unmatched_count=0,
        sheet_unmatched_count=0,
    )

    result = classify_reconciliation_rows(bank_rows=bank_rows, sheet_rows=sheet_rows, match_result=match_result)

    assert result.conciliated_count == 2
    assert result.pending_count == 0
    assert result.divergent_count == 0
    assert result.rows[0].status == "conciliado"
    assert result.rows[1].status == "conciliado"


def test_classify_reconciliation_rows_marks_unmatched_rows_as_pending() -> None:
    bank_rows = [
        NormalizedTransaction(date="2026-04-01", description="PAGAMENTO ALFA", amount=-100.0, type="outflow"),
    ]
    sheet_rows = [
        NormalizedTransaction(date="2026-04-05", description="RECEBIMENTO BETA", amount=80.0, type="inflow"),
    ]
    match_result = LedgerMatchResult(
        matches=[],
        exact_matches_count=0,
        date_tolerance_matches_count=0,
        description_similarity_matches_count=0,
        total_matches_count=0,
        bank_unmatched_count=1,
        sheet_unmatched_count=1,
    )

    result = classify_reconciliation_rows(bank_rows=bank_rows, sheet_rows=sheet_rows, match_result=match_result)

    assert result.conciliated_count == 0
    assert result.pending_count == 2
    assert result.divergent_count == 0
    assert result.rows[0].status == "pendente"
    assert result.rows[0].reason == "missing_in_sheet"
    assert result.rows[1].status == "pendente"
    assert result.rows[1].reason == "missing_in_bank"


def test_classify_reconciliation_rows_marks_amount_mismatch_as_divergent() -> None:
    bank_rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="PAGAMENTO FORNECEDOR ALFA",
            amount=-100.0,
            type="outflow",
        ),
    ]
    sheet_rows = [
        NormalizedTransaction(
            date="2026-04-02",
            description="PAGAMENTO FORNECEDOR ALFA",
            amount=-120.0,
            type="outflow",
        ),
    ]
    match_result = LedgerMatchResult(
        matches=[],
        exact_matches_count=0,
        date_tolerance_matches_count=0,
        description_similarity_matches_count=0,
        total_matches_count=0,
        bank_unmatched_count=1,
        sheet_unmatched_count=1,
    )

    result = classify_reconciliation_rows(bank_rows=bank_rows, sheet_rows=sheet_rows, match_result=match_result)

    assert result.conciliated_count == 0
    assert result.pending_count == 0
    assert result.divergent_count == 2
    assert result.rows[0].status == "divergente"
    assert result.rows[0].reason == "amount_mismatch"
    assert result.rows[1].status == "divergente"
    assert result.rows[1].reason == "amount_mismatch"


def test_classify_reconciliation_rows_marks_date_out_of_tolerance_as_divergent() -> None:
    bank_rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="PAGAMENTO FORNECEDOR ALFA",
            amount=-100.0,
            type="outflow",
        ),
    ]
    sheet_rows = [
        NormalizedTransaction(
            date="2026-04-10",
            description="PAGAMENTO FORNECEDOR ALFA",
            amount=-100.0,
            type="outflow",
        ),
    ]
    match_result = LedgerMatchResult(
        matches=[],
        exact_matches_count=0,
        date_tolerance_matches_count=0,
        description_similarity_matches_count=0,
        total_matches_count=0,
        bank_unmatched_count=1,
        sheet_unmatched_count=1,
    )

    result = classify_reconciliation_rows(bank_rows=bank_rows, sheet_rows=sheet_rows, match_result=match_result)

    assert result.conciliated_count == 0
    assert result.pending_count == 0
    assert result.divergent_count == 2
    assert result.rows[0].status == "divergente"
    assert result.rows[0].reason == "date_out_of_tolerance_window"
    assert result.rows[1].status == "divergente"
    assert result.rows[1].reason == "date_out_of_tolerance_window"
