from app.application.ledger_match_models import LedgerReconciliationRow
from app.application.reconcile_problem_engine import generate_reconciliation_problems


def test_generate_reconciliation_problems_builds_expected_operational_insights() -> None:
    rows = [
        LedgerReconciliationRow(
            row_id="sheet_001",
            source="sheet",
            date="2026-04-01",
            description="PAGAMENTO FORNECEDOR ALFA",
            amount=-120.0,
            status="pendente",
            match_rule="none",
            matched_row_id=None,
            reason="missing_in_bank",
        ),
        LedgerReconciliationRow(
            row_id="bank_001",
            source="bank",
            date="2026-04-01",
            description="RECEBIMENTO CLIENTE BETA",
            amount=300.0,
            status="pendente",
            match_rule="none",
            matched_row_id=None,
            reason="missing_in_sheet",
        ),
        LedgerReconciliationRow(
            row_id="bank_002",
            source="bank",
            date="2026-04-02",
            description="PAGAMENTO ALFA",
            amount=-100.0,
            status="divergente",
            match_rule="none",
            matched_row_id="sheet_002",
            reason="amount_mismatch",
        ),
        LedgerReconciliationRow(
            row_id="sheet_002",
            source="sheet",
            date="2026-04-02",
            description="PAGAMENTO ALFA",
            amount=-120.0,
            status="divergente",
            match_rule="none",
            matched_row_id="bank_002",
            reason="amount_mismatch",
        ),
    ]

    result = generate_reconciliation_problems(rows)

    assert [item.type for item in result] == [
        "missing_payment",
        "missing_receipt",
        "amount_mismatch",
    ]


def test_generate_reconciliation_problems_detects_possible_duplicate_groups() -> None:
    rows = [
        LedgerReconciliationRow(
            row_id="sheet_010",
            source="sheet",
            date="2026-04-05",
            description="RECEBIMENTO PIX CLIENTE X",
            amount=100.0,
            status="pendente",
            match_rule="none",
            matched_row_id=None,
            reason="missing_in_bank",
        ),
        LedgerReconciliationRow(
            row_id="sheet_011",
            source="sheet",
            date="2026-04-05",
            description="RECEBIMENTO PIX CLIENTE X",
            amount=100.0,
            status="pendente",
            match_rule="none",
            matched_row_id=None,
            reason="missing_in_bank",
        ),
    ]

    result = generate_reconciliation_problems(rows)

    assert len(result) == 1
    assert result[0].type == "possible_duplicate"


def test_generate_reconciliation_problems_returns_empty_when_no_problem_is_found() -> None:
    rows = [
        LedgerReconciliationRow(
            row_id="bank_020",
            source="bank",
            date="2026-04-10",
            description="PAGAMENTO ALFA",
            amount=-100.0,
            status="conciliado",
            match_rule="exact",
            matched_row_id="sheet_020",
            reason="matched_exact_value_and_date",
        ),
    ]

    result = generate_reconciliation_problems(rows)

    assert result == []
