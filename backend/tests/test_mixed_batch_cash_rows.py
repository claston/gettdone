"""Synthetic regressions for movements whose history mentions a balance."""

import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.reviewed_cash_columns import parse_reviewed_cash
from app.application.parsers.pdf.statement_review import statement_review_scope
from tests.test_recovery_sweep_ocr import extraction


def cash_source(description, *, amount="-10,00", balance="90,00"):
    return extraction([
        ("Data", 0.08, 0.2), ("Histórico", 0.16, 0.2),
        ("Valor (R$)", 0.71, 0.2), ("Saldo (R$)", 0.87, 0.2),
        ("SALDO ANTERIOR", 0.16, 0.15), ("100,00", 0.88, 0.15),
        ("02/09/2026", 0.05, 0.3), (description, 0.16, 0.3),
        (amount, 0.73, 0.3), (balance, 0.88, 0.3),
    ])


@pytest.mark.parametrize("description", ["Saldo Vinculado Liberado", "Saldo transferido para aplicação"])
@pytest.mark.parametrize("amount,balance,expected", [("-10,00", "90,00", -10), ("10,00", "110,00", 10)])
def test_balance_word_does_not_replace_explicit_movement_columns(description, amount, balance, expected):
    statement = parse_reviewed_cash(cash_source(description, amount=amount, balance=balance), "santander_app")
    assert [(r.transaction.date, r.transaction.description, r.transaction.amount) for r in statement.rows] == [
        ("2026-09-02", description, expected),
    ]
    assert statement.rows[0].source_page == 1
    assert statement.opening == 100
    assert statement.closing == 100 + expected


@pytest.mark.parametrize("description", ["SALDO DO DIA", "SALDO ANTERIOR", "SALDO FINAL"])
def test_actual_balance_control_with_two_values_remains_ambiguous(description):
    with statement_review_scope(), pytest.raises(PdfFinancialValidationError):
        parse_reviewed_cash(cash_source(description), "santander_app")


def test_readable_movement_with_disagreeing_balance_requires_review():
    with statement_review_scope() as warnings:
        statement = parse_reviewed_cash(cash_source("Saldo Vinculado Liberado", balance="95,00"), "santander_app")
    assert [r.transaction.amount for r in statement.rows] == [-10]
    assert "financial_validation_failed" in warnings
