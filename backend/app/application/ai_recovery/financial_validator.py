from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from app.application.ai_recovery.models import (
    AIStatement,
    AITransaction,
    FinancialValidationDisposition,
    FinancialValidationIssue,
    FinancialValidationResult,
    TransactionDirection,
)

FINANCIAL_VALIDATOR_RULE_VERSION = "2026-09-20.v1"
BALANCE_TOLERANCE = Decimal("0.01")
_BALANCE_ROW_LABELS = (
    "saldo anterior",
    "saldo inicial",
    "saldo final",
    "saldo atual",
    "saldo disponivel",
    "saldo do dia",
    "saldo em conta",
    "saldo bloqueado",
    "saldo total",
)


class FinancialValidator:
    """Deterministic checks that gate whether an AI transcription may continue."""

    def validate(
        self,
        statement: AIStatement,
        *,
        expected_page_count: int | None = None,
    ) -> FinancialValidationResult:
        errors: list[FinancialValidationIssue] = []
        warnings: list[FinancialValidationIssue] = []
        transactions = statement.transactions

        if not transactions:
            errors.append(FinancialValidationIssue("no_transactions"))

        valid_period = True
        if statement.period_start is not None and statement.period_end is not None:
            valid_period = statement.period_start <= statement.period_end
            if not valid_period:
                errors.append(FinancialValidationIssue("invalid_statement_period"))

        for index, transaction in enumerate(transactions):
            self._validate_transaction(
                transaction,
                index=index,
                statement=statement,
                valid_period=valid_period,
                expected_page_count=expected_page_count,
                errors=errors,
            )

        suspected_duplicates_count = self._detect_page_boundary_duplicates(transactions, errors=errors)
        balance_checks_count, has_balance_evidence = self._validate_balances(statement, errors=errors)

        if errors:
            disposition = FinancialValidationDisposition.REJECTED
            score = Decimal("0.00")
        elif not has_balance_evidence:
            warnings.append(FinancialValidationIssue("insufficient_balance_evidence"))
            disposition = FinancialValidationDisposition.INCONCLUSIVE
            score = Decimal("0.70")
        else:
            disposition = FinancialValidationDisposition.APPROVED
            has_bounds = statement.opening_balance is not None and statement.closing_balance is not None
            has_full_running_balances = len(transactions) >= 2 and all(
                transaction.running_balance is not None for transaction in transactions
            )
            if has_bounds and has_full_running_balances:
                score = Decimal("0.99")
            elif has_bounds:
                score = Decimal("0.95")
            else:
                score = Decimal("0.93")

        return FinancialValidationResult(
            disposition=disposition,
            score=score,
            rule_version=FINANCIAL_VALIDATOR_RULE_VERSION,
            transactions_count=len(transactions),
            balance_checks_count=balance_checks_count,
            suspected_duplicates_count=suspected_duplicates_count,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _validate_transaction(
        self,
        transaction: AITransaction,
        *,
        index: int,
        statement: AIStatement,
        valid_period: bool,
        expected_page_count: int | None,
        errors: list[FinancialValidationIssue],
    ) -> None:
        if transaction.date is None:
            errors.append(FinancialValidationIssue("missing_transaction_date", index))
        elif valid_period:
            if statement.period_start is not None and transaction.date < statement.period_start:
                errors.append(FinancialValidationIssue("transaction_outside_statement_period", index))
            elif statement.period_end is not None and transaction.date > statement.period_end:
                errors.append(FinancialValidationIssue("transaction_outside_statement_period", index))

        if transaction.direction == TransactionDirection.UNKNOWN:
            errors.append(FinancialValidationIssue("unknown_transaction_direction", index))
        if transaction.amount <= 0:
            errors.append(FinancialValidationIssue("non_positive_transaction_amount", index))
        if _is_balance_row(transaction.description):
            errors.append(FinancialValidationIssue("balance_row_detected", index))
        if expected_page_count is not None and transaction.source_page > expected_page_count:
            errors.append(FinancialValidationIssue("source_page_out_of_range", index))

    def _detect_page_boundary_duplicates(
        self,
        transactions: list[AITransaction],
        *,
        errors: list[FinancialValidationIssue],
    ) -> int:
        count = 0
        for index in range(1, len(transactions)):
            previous = transactions[index - 1]
            current = transactions[index]
            crosses_page_boundary = current.source_page == previous.source_page + 1
            if crosses_page_boundary and _transaction_fingerprint(current) == _transaction_fingerprint(previous):
                count += 1
                errors.append(FinancialValidationIssue("suspected_page_boundary_duplicate", index))
        return count

    def _validate_balances(
        self,
        statement: AIStatement,
        *,
        errors: list[FinancialValidationIssue],
    ) -> tuple[int, bool]:
        transactions = statement.transactions
        checks_count = 0
        has_bounds = statement.opening_balance is not None and statement.closing_balance is not None

        if has_bounds:
            checks_count += 1
            expected_closing = statement.opening_balance + sum(
                (_signed_amount(transaction) for transaction in transactions),
                start=Decimal("0"),
            )
            if not _money_matches(expected_closing, statement.closing_balance):
                errors.append(FinancialValidationIssue("closing_balance_mismatch"))

        previous_balance = statement.opening_balance
        pending_change = Decimal("0")
        for index, transaction in enumerate(transactions):
            pending_change += _signed_amount(transaction)
            if transaction.running_balance is None:
                continue
            if previous_balance is not None:
                checks_count += 1
                if not _money_matches(previous_balance + pending_change, transaction.running_balance):
                    errors.append(FinancialValidationIssue("running_balance_mismatch", index))
            previous_balance = transaction.running_balance
            pending_change = Decimal("0")

        if statement.closing_balance is not None and previous_balance is not None and not has_bounds:
            checks_count += 1
            if not _money_matches(previous_balance + pending_change, statement.closing_balance):
                errors.append(FinancialValidationIssue("closing_balance_mismatch"))

        has_full_running_sequence = (
            len(transactions) >= 2
            and all(transaction.running_balance is not None for transaction in transactions)
            and checks_count >= len(transactions) - 1
        )
        has_opening_to_last_running_proof = bool(
            transactions
            and statement.opening_balance is not None
            and transactions[-1].running_balance is not None
        )
        return checks_count, has_bounds or has_full_running_sequence or has_opening_to_last_running_proof


def _signed_amount(transaction: AITransaction) -> Decimal:
    if transaction.direction == TransactionDirection.DEBIT:
        return -transaction.amount
    return transaction.amount


def _money_matches(expected: Decimal, actual: Decimal) -> bool:
    return abs(expected - actual) <= BALANCE_TOLERANCE


def _transaction_fingerprint(transaction: AITransaction) -> tuple[object, ...]:
    return (
        transaction.date,
        _normalize_text(transaction.description),
        transaction.amount,
        transaction.direction,
    )


def _is_balance_row(description: str) -> bool:
    normalized = _normalize_text(description)
    return any(normalized == label or normalized.startswith(f"{label} ") for label in _BALANCE_ROW_LABELS)


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.casefold()).strip()
