from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.application.ai_recovery.financial_validator import FinancialValidator
from app.application.ai_recovery.models import (
    AIStatement,
    AITransaction,
    FinancialValidationDisposition,
    TransactionDirection,
)


def _transaction(
    *,
    transaction_date: date | None = date(2026, 8, 2),
    description: str = "PIX recebido",
    amount: str = "500.00",
    direction: TransactionDirection = TransactionDirection.CREDIT,
    running_balance: str | None = None,
    source_page: int = 1,
    source_line: int = 1,
) -> AITransaction:
    return AITransaction(
        date=transaction_date,
        description=description,
        amount=Decimal(amount),
        direction=direction,
        running_balance=Decimal(running_balance) if running_balance is not None else None,
        source_page=source_page,
        source_line=source_line,
    )


def test_validator_approves_reconciled_opening_and_closing_balances() -> None:
    statement = AIStatement(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1300.00"),
        transactions=[
            _transaction(),
            _transaction(
                transaction_date=date(2026, 8, 3),
                description="Pagamento de boleto",
                amount="200.00",
                direction=TransactionDirection.DEBIT,
                source_line=2,
            ),
        ],
    )

    result = FinancialValidator().validate(statement, expected_page_count=1)

    assert result.disposition == FinancialValidationDisposition.APPROVED
    assert result.approved is True
    assert result.balance_checks_count == 1
    assert result.score == Decimal("0.95")
    assert result.errors == ()


def test_validator_rejects_balance_mismatch_over_one_cent() -> None:
    statement = AIStatement(
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1300.00"),
        transactions=[
            _transaction(),
            _transaction(
                description="Pagamento extraído com valor incorreto",
                amount="20.00",
                direction=TransactionDirection.DEBIT,
                source_line=2,
            ),
        ],
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.REJECTED
    assert "closing_balance_mismatch" in result.error_codes
    assert result.score == Decimal("0.00")


def test_validator_accepts_exactly_one_cent_of_balance_tolerance() -> None:
    statement = AIStatement(
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1500.01"),
        transactions=[_transaction()],
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.APPROVED


def test_validator_marks_statement_without_balance_evidence_as_inconclusive() -> None:
    result = FinancialValidator().validate(AIStatement(transactions=[_transaction()]))

    assert result.disposition == FinancialValidationDisposition.INCONCLUSIVE
    assert result.approved is False
    assert result.error_codes == ()
    assert "insufficient_balance_evidence" in result.warning_codes
    assert result.score == Decimal("0.70")


def test_validator_approves_complete_consistent_running_balance_sequence() -> None:
    statement = AIStatement(
        transactions=[
            _transaction(running_balance="1500.00"),
            _transaction(
                transaction_date=date(2026, 8, 3),
                description="Pagamento",
                amount="200.00",
                direction=TransactionDirection.DEBIT,
                running_balance="1300.00",
                source_line=2,
            ),
        ]
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.APPROVED
    assert result.balance_checks_count == 1
    assert result.score == Decimal("0.93")


def test_validator_rejects_inconsistent_running_balance() -> None:
    statement = AIStatement(
        opening_balance=Decimal("1000.00"),
        transactions=[_transaction(running_balance="1400.00")],
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.REJECTED
    assert "running_balance_mismatch" in result.error_codes


@pytest.mark.parametrize(
    ("transaction", "error_code"),
    [
        (_transaction(transaction_date=None), "missing_transaction_date"),
        (_transaction(direction=TransactionDirection.UNKNOWN), "unknown_transaction_direction"),
        (_transaction(amount="0.00"), "non_positive_transaction_amount"),
        (_transaction(description="Saldo anterior"), "balance_row_detected"),
        (_transaction(transaction_date=date(2026, 9, 1)), "transaction_outside_statement_period"),
    ],
)
def test_validator_rejects_unsafe_transaction_content(transaction: AITransaction, error_code: str) -> None:
    statement = AIStatement(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1500.00"),
        transactions=[transaction],
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.REJECTED
    assert error_code in result.error_codes


def test_validator_rejects_suspected_duplicate_at_page_boundary_without_removing_it() -> None:
    first = _transaction(source_page=1, source_line=40)
    repeated = _transaction(source_page=2, source_line=1)
    statement = AIStatement(
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("2000.00"),
        transactions=[first, repeated],
    )

    result = FinancialValidator().validate(statement, expected_page_count=2)

    assert result.disposition == FinancialValidationDisposition.REJECTED
    assert "suspected_page_boundary_duplicate" in result.error_codes
    assert result.suspected_duplicates_count == 1
    assert result.transactions_count == 2


def test_validator_does_not_treat_same_day_equal_transactions_on_one_page_as_duplicates() -> None:
    statement = AIStatement(
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("2000.00"),
        transactions=[_transaction(source_line=1), _transaction(source_line=2)],
    )

    result = FinancialValidator().validate(statement)

    assert result.disposition == FinancialValidationDisposition.APPROVED
    assert result.suspected_duplicates_count == 0


def test_validator_rejects_invalid_period_and_source_page() -> None:
    statement = AIStatement(
        period_start=date(2026, 8, 31),
        period_end=date(2026, 8, 1),
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1500.00"),
        transactions=[_transaction(source_page=3)],
    )

    result = FinancialValidator().validate(statement, expected_page_count=2)

    assert result.disposition == FinancialValidationDisposition.REJECTED
    assert "invalid_statement_period" in result.error_codes
    assert "source_page_out_of_range" in result.error_codes


def test_ai_models_reject_binary_float_amounts() -> None:
    with pytest.raises(ValidationError):
        AITransaction(
            date=date(2026, 8, 2),
            description="PIX",
            amount=10.5,
            direction="credit",
            source_page=1,
        )


def test_ai_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AITransaction(
            date=date(2026, 8, 2),
            description="PIX",
            amount="10.50",
            direction="credit",
            source_page=1,
            unexpected="value",
        )


def test_statement_warnings_do_not_share_mutable_defaults() -> None:
    first = AIStatement(transactions=[_transaction()])
    second = AIStatement(transactions=[_transaction()])

    first.warnings.append("first-only")

    assert second.warnings == []
