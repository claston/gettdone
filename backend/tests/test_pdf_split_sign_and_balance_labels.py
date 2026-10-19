import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.pdf_parser import (
    _flatten_statement_lines,
    _parse_pdf_transactions_from_page_texts,
    _parse_tabular_statement_rows,
)
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def flash_statement_lines(*, sign="-", amount="R$\u00a01.173,00", description="ESTABELECIMENTO EXEMPLO", separator=()):
    return (
        "FLASH", "EXTRATO DE MOVIMENTAÇÕES", "DATA MOVIMENTAÇÃO VALOR PESSOA PAGAMENTO",
        "19/03/2026", "18:05", description, sign, *separator, amount,
        "PESSOA SINTÉTICA", "Cartão virtual - Final 0000 Finalizada",
        "18/03/2026", "12:31", "Depósito +", "R$ 1.173,00", "Pix Finalizado",
    )


def bb_statement_lines(balance_label="S A L D O"):
    return (
        "EXTRATO CONTA CORRENTE", "Período: dezembro 2020", "Data Histórico Valor Saldo",
        "01/12/2020 SALDO ANTERIOR 66,70 C",
        "30/12/2020 PIX RECEBIDO CLIENTE 1.000,00 C",
        "31/12/2020 TARIFA BANCÁRIA 2,55 D",
        f"31/12/2020 {balance_label} 1.064,15 C",
    )


@pytest.mark.parametrize("sign,expected", [("-", -1173.0), ("−", -1173.0), ("+", 1173.0)])
@pytest.mark.parametrize("amount", ["R$\u00a01.173,00", "R$ 1173,00"])
def test_grouped_amount_preserves_an_adjacent_standalone_sign(sign, expected, amount):
    result = _parse_pdf_transactions_from_page_texts(["\n".join(flash_statement_lines(sign=sign, amount=amount))])
    assert len(result.transactions) == 2
    assert result.transactions[0].amount == expected
    assert result.transactions[0].type == ("outflow" if expected < 0 else "inflow")
    assert result.transactions[0].date == "2026-03-19"
    assert result.transactions[1].amount == 1173.0
    assert result.transactions[1].date == "2026-03-18"
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 8


@pytest.mark.parametrize("sign,description,expected", [("-", "ESTORNO SINTÉTICO", -1173.0), ("+", "PAGAMENTO SINTÉTICO", 1173.0)])
def test_standalone_sign_takes_precedence_over_description_hints(sign, description, expected):
    lines = flash_statement_lines(sign=sign, description=description)
    result = _parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    assert [row.amount for row in result.transactions] == [expected, 1173.0]


@pytest.mark.parametrize("amount,expected", [("+ R$ 1.173,00", 1173.0), ("- R$ 1.173,00", -1173.0),
                                             ("R$ -1.173,00", -1173.0), ("1.173,00 C", 1173.0), ("1.173,00 D", -1173.0)])
def test_existing_amount_signs_are_not_replaced_by_a_preceding_dash(amount, expected):
    result = _parse_pdf_transactions_from_page_texts(["\n".join(flash_statement_lines(amount=amount))])
    assert [row.amount for row in result.transactions] == [expected, 1173.0]


@pytest.mark.parametrize("sign,separator", [("--", ()), ("-", ("",)), ("-", ("REFERÊNCIA SINTÉTICA",))])
def test_separators_and_nonadjacent_dashes_do_not_change_unsigned_amounts(sign, separator):
    lines = flash_statement_lines(sign=sign, separator=separator)
    result = _parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    assert [row.amount for row in result.transactions] == [1173.0, 1173.0]


def test_a_dash_before_a_bare_number_is_not_assumed_to_be_an_amount_sign():
    lines = flash_statement_lines(amount="1.173,00")
    result = _parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    assert [row.amount for row in result.transactions] == [1173.0, 1173.0]


def test_a_dash_before_a_running_balance_does_not_negate_it():
    text = "\n".join((
        "EXTRATO CONTA CORRENTE", "01/09/2026", "CLIENTE SINTÉTICO", "R$ 100,00", "-", "R$ 1.200,00",
        "02/09/2026", "FORNECEDOR SINTÉTICO", "- R$ 25,00", "R$ 1.175,00",
    ))
    result = _parse_pdf_transactions_from_page_texts([text])
    assert [row.amount for row in result.transactions] == [100.0, -25.0]
    assert [row.running_balance for row in result.canonical_transactions] == [1200.0, 1175.0]
    assert all(not row.warnings for row in result.canonical_transactions)


def test_spaced_closing_balance_is_also_excluded_by_tabular_parsing():
    lines = _flatten_statement_lines(["\n".join(bb_statement_lines())])
    rows, _ = _parse_tabular_statement_rows(lines)
    assert [row.transaction.amount for row in rows] == [66.70, 1000.0, -2.55]
    assert rows[0].transaction.description == "SALDO ANTERIOR"


@pytest.mark.parametrize("balance_label", ["S A L D O", "S\u00a0A\u00a0L\u00a0D\u00a0O", "S  AL DO", "SALDO"])
def test_spaced_closing_balance_is_not_imported_as_a_transaction(balance_label, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((bb_statement_lines(balance_label),))
    result = build_default_conversion_pipeline().run(filename="bb.pdf", raw_bytes=raw, analysis_id="an_spaced_balance")
    analysis = result.analysis_data
    rows = analysis.report_transactions or analysis.preview_transactions
    assert analysis.transactions_total == 2
    assert [row.amount for row in rows] == [1000.0, -2.55]
    assert analysis.net_total == 997.45
    assert analysis.opening_balance == 66.70
    assert analysis.closing_balance == 1064.15
    assert all(not row.warning_types for row in rows)


def test_native_flash_pdf_preserves_both_movements_and_the_net_total(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((flash_statement_lines(),))
    analysis = build_default_conversion_pipeline().run(
        filename="flash.pdf", raw_bytes=raw, analysis_id="an_split_sign",
    ).analysis_data
    rows = analysis.report_transactions or analysis.preview_transactions
    assert analysis.transactions_total == 2
    assert [row.amount for row in rows] == [-1173.0, 1173.0]
    assert analysis.net_total == 0
    assert all(not row.warning_types for row in rows)
