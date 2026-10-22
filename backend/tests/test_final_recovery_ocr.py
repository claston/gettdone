"""Geometry, signed values and controls with synthetic bank statements."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.test_recovery_sweep_ocr import extraction


def sicredi():
    return extraction([
        ("Sicredi Sicredifone Cooperativa:", .1, .02),
        ("Extrato (Período de 01/09/2026 a 30/09/2026)", .1, .06),
        ("Data", .1, .1), ("Descrição", .2, .1), ("Documento", .6, .1),
        ("Valor (R$)", .75, .1), ("Saldo (R$)", .88, .1),
        ("SALDO ANTERIOR", .2, .15), ("0,00", .88, .15),
        ("01/09/2026", .1, .2), ("CRÉDITO TESTE", .2, .2), ("100001", .6, .2),
        ("50.000,00", .75, .2), ("50.000,00", .88, .2),
        ("02/09/2026", .1, .25), ("PAGAMENTO TESTE", .2, .25), ("100002", .6, .25),
        ("-100,00", .75, .25), ("49.900,00", .88, .25),
        ("Lançamentos Futuros", .1, .3),
        ("01/10/2026", .1, .35), ("DDA Pagar Boletos", .2, .35), ("-500,00", .75, .35),
    ])


def test_sicredi_ocr_preserves_signs_all_row_balances_and_excludes_future_section():
    result = pdf_parser._parse_textract_text_pages(sicredi())
    assert len(result.transactions) == 2
    assert [t.amount for t in result.transactions] == [50000, -100]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 49900


def test_sicredi_ocr_rejects_inconsistent_balance_and_orphan_amount():
    source = sicredi()
    changed = [replace(line, text="49.901,00") if line.text == "49.900,00" else line for line in source.pages[0].lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(source.pages[0], lines=changed)]))


def test_bradesco_scan_small_column_skew_does_not_hide_opening_balance():
    from tests.test_oct20_followup_ocr import bradesco
    source = bradesco()
    first = source.pages[0]
    opening = next(line for line in first.lines if line.text == "SALDO ANTERIOR")
    lines = [replace(line, bbox={**line.bbox, "top": opening.bbox["top"] - .0045})
             if line.bbox and line.bbox["left"] > .75 and abs(line.bbox["top"] - opening.bbox["top"]) < .002
             else line for line in first.lines]
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(first, lines=lines), *source.pages[1:]]))
    assert result.parse_metrics["financial_controls_validated"] == 1


def bb_combined():
    return extraction([
        ("Banco do Brasil Extrato de Conta Corrente", .1, .02),
        ("Movimentação em Setembro de 2026", .1, .05),
        ("Data", .08, .1), ("Origem", .17, .1), ("Histórico", .25, .1),
        ("Documento", .67, .1), ("Lote", .78, .1), ("Valor R$", .87, .1),
        ("02/09/2026", .08, .2), ("3370-7", .17, .2), ("Saldo do dia", .25, .2),
        ("13105", .78, .2), ("9.000,00 C", .87, .2),
        ("Conta corrente + Rende Fácil", .25, .22),
        ("02/09/2026", .08, .3), ("3370-7", .17, .3), ("Pagamento de Boleto", .25, .3),
        ("100001", .67, .3), ("13105", .78, .3), ("50,00D", .87, .3),
        ("COMPLEMENTO TESTE", .25, .32),
        ("bb.com.br Central de Atendimento BB", .1, .9), ("Pág. 1", .9, .95),
    ])


def test_bb_combined_ocr_keeps_cd_and_complements_without_comparing_combined_balances():
    result = pdf_parser._parse_textract_text_pages(bb_combined())
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -50
    assert "COMPLEMENTO TESTE" in result.transactions[0].description
    assert result.parse_metrics["balance_validation_status"] == "combined_cash_investment_not_comparable"


def caixa_page(start, end, date, amount, balance):
    return extraction([
        ("GERENCIADOR CAIXA Extrato por período Via internet", .1, .02),
        (f"Lançamentos de {end} à {start}", .1, .06),
        ("LANÇAMENTO", .1, .1), ("MOVIMENTO", .3, .1), ("DOCUMENTO", .49, .1),
        ("HISTÓRICO", .64, .1), ("VALOR", .79, .1), ("SALDO", .91, .1),
        (date, .13, .2), (date, .33, .2), ("100001", .52, .2), ("CRÉDITO TESTE", .64, .2),
        (amount, .80, .2), (balance, .93, .2),
        (date, .13, .25), (date, .33, .25), ("000000", .52, .25), ("SALDO DIA", .64, .25),
        ("0.00", .80, .25), (balance, .93, .25),
    ]).pages[0]


def test_caixa_ocr_resets_balance_at_each_separate_period_and_uses_movement_date():
    first = caixa_page("01/01/2024", "31/01/2024", "02/01/2024", "100.00", "150.00")
    second = replace(caixa_page("01/04/2024", "30/04/2024", "02/04/2024", "-20.00", "980.00"), page_number=2)
    source = replace(extraction([]), pages=[first, second])
    result = pdf_parser._parse_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [100, -20]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["financial_controls_validated"] == 0  # Each opening is derived, never printed.
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
