from datetime import date
from decimal import Decimal

from app.application.ai_recovery.comparator import (
    ComparisonLedger,
    ComparisonTransaction,
    RecoveryDiagnosisCode,
    compare_recovery_ledgers,
)
from app.application.ai_recovery.models import TransactionDirection


def _tx(
    day: int | None,
    description: str | tuple[str, ...],
    amount: str,
    direction: TransactionDirection,
    *,
    running_balance: str | None = None,
    evidence: tuple[str, ...] = (),
    page: int = 1,
) -> ComparisonTransaction:
    lines = (description,) if isinstance(description, str) else description
    return ComparisonTransaction(
        date=date(2026, 8, day) if day is not None else None,
        description_lines=lines,
        amount=Decimal(amount),
        direction=direction,
        running_balance=Decimal(running_balance) if running_balance is not None else None,
        page=page,
        evidence_ids=evidence,
    )


def _ledger(*transactions: ComparisonTransaction, opening: str | None = None, closing: str | None = None):
    return ComparisonLedger(
        transactions=transactions,
        opening_balance=Decimal(opening) if opening is not None else None,
        closing_balance=Decimal(closing) if closing is not None else None,
    )


def test_comparator_reports_exact_evidence_aligned_ledgers() -> None:
    transaction = _tx(
        2,
        "PIX recebido",
        "500.00",
        TransactionDirection.CREDIT,
        running_balance="1500.00",
        evidence=("p1:l8-l9",),
    )

    result = compare_recovery_ledgers(
        deterministic=_ledger(transaction, opening="1000.00", closing="1500.00"),
        nova=_ledger(transaction, opening="1000.00", closing="1500.00"),
    )

    assert result.exact is True
    assert result.financially_equivalent is True
    assert result.matches[0].deterministic_index == 0
    assert result.matches[0].nova_index == 0
    assert result.matches[0].unique is True
    assert result.evidence_coverage == Decimal("1.0000")
    assert result.diagnosis_codes == ()


def test_sequence_alignment_keeps_rows_after_a_missing_transaction_aligned() -> None:
    first = _tx(2, "PIX recebido", "100.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    missing = _tx(3, "Pagamento", "40.00", TransactionDirection.DEBIT, evidence=("p1:l9",))
    last = _tx(4, "Tarifa", "10.00", TransactionDirection.DEBIT, evidence=("p1:l10",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(first, last),
        nova=_ledger(first, missing, last),
    )

    assert [(match.deterministic_index, match.nova_index) for match in result.matches] == [(0, 0), (1, 2)]
    assert result.deterministic_unmatched == ()
    assert result.nova_unmatched == (1,)
    assert result.diagnosis_codes == (RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION,)


def test_comparator_classifies_sign_inversion() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.DEBIT, evidence=("p1:l8",))
    nova = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.SIGN_INVERTED,)


def test_comparator_classifies_duplicate_deterministic_row_from_same_evidence() -> None:
    first = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    duplicate = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    last = _tx(3, "Pagamento", "20.00", TransactionDirection.DEBIT, evidence=("p1:l9",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(first, duplicate, last),
        nova=_ledger(first, last),
    )

    assert result.deterministic_unmatched == (1,)
    assert RecoveryDiagnosisCode.DETERMINISTIC_DUPLICATE_TRANSACTION in result.diagnosis_codes


def test_comparator_classifies_running_balance_used_as_amount() -> None:
    deterministic = _tx(
        2,
        "PIX recebido",
        "1500.00",
        TransactionDirection.CREDIT,
        running_balance="1500.00",
        evidence=("p1:l8",),
    )
    nova = _tx(
        2,
        "PIX recebido",
        "500.00",
        TransactionDirection.CREDIT,
        running_balance="1500.00",
        evidence=("p1:l8",),
    )

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.RUNNING_BALANCE_USED_AS_AMOUNT,)


def test_comparator_classifies_opening_balance_imported_as_transaction() -> None:
    opening_row = _tx(None, "Saldo anterior", "1000.00", TransactionDirection.CREDIT, evidence=("p1:l5",))
    transaction = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(opening_row, transaction, opening="1000.00"),
        nova=_ledger(transaction, opening="1000.00"),
    )

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.OPENING_BALANCE_USED_AS_TRANSACTION,)


def test_comparator_classifies_generic_column_shift_when_amounts_differ() -> None:
    deterministic = _tx(2, "PIX recebido", "123.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.COLUMN_SHIFTED,)


def test_comparator_treats_one_cent_difference_as_a_real_amount_divergence() -> None:
    deterministic = _tx(2, "PIX recebido", "500.01", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.financially_equivalent is False
    assert result.diagnosis_codes == (RecoveryDiagnosisCode.COLUMN_SHIFTED,)


def test_comparator_preserves_multiline_description_diagnosis() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8-l9",))
    nova = _tx(
        2,
        ("PIX recebido", "Cliente sintético"),
        "500.00",
        TransactionDirection.CREDIT,
        evidence=("p1:l8-l9",),
    )

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.MULTILINE_DESCRIPTION_SPLIT,)


def test_comparator_classifies_date_associated_to_wrong_row() -> None:
    deterministic = _tx(3, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.DATE_ASSOCIATED_TO_WRONG_ROW,)


def test_comparator_detects_transaction_order_inversion() -> None:
    first = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    second = _tx(3, "Pagamento", "100.00", TransactionDirection.DEBIT, evidence=("p1:l9",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(first, second),
        nova=_ledger(second, first),
    )

    assert RecoveryDiagnosisCode.TRANSACTION_ORDER_INVERTED in result.diagnosis_codes


def test_comparator_classifies_repeated_header_as_deterministic_extra() -> None:
    header = _tx(
        None,
        "Data Histórico Valor Saldo",
        "1.00",
        TransactionDirection.UNKNOWN,
        evidence=("p2:l1",),
        page=2,
    )
    transaction = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p2:l4",), page=2)

    result = compare_recovery_ledgers(
        deterministic=_ledger(header, transaction),
        nova=_ledger(transaction),
    )

    assert result.diagnosis_codes == (RecoveryDiagnosisCode.REPEATED_HEADER_USED_AS_DATA,)


def test_comparator_fails_closed_when_a_match_is_not_unique() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=())
    nova_first = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova_second = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l9",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(deterministic),
        nova=_ledger(nova_first, nova_second),
    )

    assert result.matches[0].unique is False
    assert RecoveryDiagnosisCode.AMBIGUOUS_ALIGNMENT in result.diagnosis_codes
    assert RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION not in result.diagnosis_codes


def test_comparator_does_not_hide_opposite_missing_transactions_behind_matching_bounds() -> None:
    first = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    missing_credit = _tx(3, "Estorno", "100.00", TransactionDirection.CREDIT, evidence=("p1:l9",))
    missing_debit = _tx(4, "Pagamento", "100.00", TransactionDirection.DEBIT, evidence=("p1:l10",))

    result = compare_recovery_ledgers(
        deterministic=_ledger(first, opening="1000.00", closing="1500.00"),
        nova=_ledger(first, missing_credit, missing_debit, opening="1000.00", closing="1500.00"),
    )

    assert result.financially_equivalent is False
    assert result.nova_unmatched == (1, 2)
    assert [
        diagnosis.code
        for diagnosis in result.diagnoses
        if diagnosis.code == RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION
    ] == [
        RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION,
        RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION,
    ]


def test_comparator_keeps_unrelated_and_untraced_ai_row_unexplained() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(20, "Tarifa bancária", "999.00", TransactionDirection.DEBIT, evidence=())

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.matches == ()
    assert result.diagnosis_codes == (
        RecoveryDiagnosisCode.UNEXPLAINED_DETERMINISTIC_EXTRA,
        RecoveryDiagnosisCode.UNEXPLAINED_AI_EXTRA,
    )


def test_comparator_explains_financial_match_with_different_description() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(2, "Transferência recebida", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.financially_equivalent is True
    assert result.exact is False
    assert result.diagnosis_codes == (RecoveryDiagnosisCode.DESCRIPTION_MISMATCH,)


def test_comparator_explains_financial_match_with_different_evidence() -> None:
    deterministic = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l8",))
    nova = _tx(2, "PIX recebido", "500.00", TransactionDirection.CREDIT, evidence=("p1:l9",))

    result = compare_recovery_ledgers(deterministic=_ledger(deterministic), nova=_ledger(nova))

    assert result.financially_equivalent is True
    assert result.exact is False
    assert result.diagnosis_codes == (RecoveryDiagnosisCode.EVIDENCE_MISMATCH,)


def test_comparison_transactions_reject_binary_float_money() -> None:
    try:
        ComparisonTransaction(
            date=date(2026, 8, 2),
            description_lines=("PIX",),
            amount=500.0,
            direction=TransactionDirection.CREDIT,
        )
    except (TypeError, ValueError) as exc:
        assert "Decimal" in str(exc)
    else:
        raise AssertionError("ComparisonTransaction must reject binary float amounts.")
