"""Split cash/control rows use printed evidence before financial reconciliation."""

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf


def sicoob_split_row(*, kind="balance", split=True, mismatch=False, missing_sign=False, extra_amount=False,
                     conflicting_sign=False, nonadjacent=False, opening_control=False):
    first = [
        ("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .19, .03),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .19, .06),
        ("EXTRATO CONTA CORRENTE", .35, .09), ("COOP.: 1234-0 / SICOOB FICTÍCIO", .19, .12),
        ("CONTA: 12345-6 / EMPRESA FICTÍCIA", .19, .15),
        ("PERÍODO: 01/07/2026 - 31/07/2026", .19, .18),
        ("HISTÓRICO DE MOVIMENTAÇÃO", .25, .21),
        ("DATA", .19, .24), ("HISTÓRICO", .26, .24), ("VALOR", .75, .24),
        ("30/06", .19, .28), ("SALDO ANTERIOR", .26, .28), ("100,00C", .75, .28),
        ("01/07", .19, .80), ("PIX RECEBIDO", .26, .80), ("50,00C", .75, .80),
        ("DOC.: 1001", .26, .82),
    ]
    if kind == "balance":
        first += [
            ("01/07", .19, .85), ("SALDO DO DIA", .26, .85), ("150,00C", .75, .85),
            ("02/07", .19, .90), ("PIX EMIT.OUTRA IF", .26, .90), ("20,00D", .75, .893),
            ("DOC.: 1002", .26, .92),
        ]
        label, amount, sign = "SALDO DO DIA", "131,00" if mismatch else "130,00", "C"
    else:
        first += [("01/07", .19, .90), ("SALDO DO DIA", .26, .90), ("150,00C", .75, .893)]
        label, amount, sign = "PIX EMIT.OUTRA IF", "20,00", "D"
    second = [("02/07", .19, .05), (label, .26, .05)]
    if opening_control:
        second[1] = ("SALDO ANTERIOR", .26, .05)
    if not missing_sign:
        second.append((sign, .81, .05))
    if conflicting_sign:
        second.append(("D" if sign == "C" else "C", .825, .05))
    if split:
        first.append((amount, .75, .95))
    else:
        second.append((amount, .75, .043))
    if extra_amount:
        first.append(("9,00", .75, .97))
    if kind != "balance":
        second += [("DOC.: 1002", .26, .07), ("02/07", .19, .11),
                   ("SALDO DO DIA", .26, .11), ("130,00C", .75, .103)]
        next_y = .16
    else:
        next_y = .09
    second += [
        ("03/07", .19, next_y), ("PIX RECEBIDO", .26, next_y), ("10,00C", .75, next_y - .007),
        ("DOC.: 1003", .26, next_y + .02),
        ("03/07", .19, .30), ("SALDO DO DIA", .26, .30), ("140,00C", .75, .30),
        ("RESUMO", .45, .60), ("(+) SALDO EM CONTA:", .19, .64), ("140,00C", .75, .64),
    ]
    return grid_pdf([first, [], second] if nonadjacent else [first, second])


@pytest.mark.parametrize("kind", ["balance", "transaction"])
def test_split_row_matches_unbroken_statement_with_reconciled_balances(kind):
    result = pdf_parser.parse_pdf_transactions(sicoob_split_row(kind=kind))
    control = pdf_parser.parse_pdf_transactions(sicoob_split_row(kind=kind, split=False))
    assert result.layout.layout_name == "sicoob_sisbr_native_split_cd_v1"
    assert [(t.date, t.amount, t.description) for t in result.transactions] == [
        (t.date, t.amount, t.description) for t in control.transactions
    ]
    assert [t.amount for t in result.transactions] == [50, -20, 10]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["verified_closing_balance"] == 140
    assert {page for page, _ in result.canonical_transactions[1].source_line_refs} == {1, 2}


def test_recovered_control_mismatch_keeps_movements_and_requires_review():
    result = pdf_parser.parse_pdf_transactions(sicoob_split_row(mismatch=True))
    assert [t.amount for t in result.transactions] == [50, -20, 10]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("kwargs", [
    {"missing_sign": True}, {"extra_amount": True}, {"conflicting_sign": True},
    {"nonadjacent": True}, {"opening_control": True},
])
def test_ambiguous_fragment_cannot_use_balance_arithmetic_to_choose_a_value(kwargs):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(sicoob_split_row(**kwargs))
