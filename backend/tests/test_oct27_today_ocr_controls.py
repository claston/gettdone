"""OCR repairs require printed numeric witnesses and preserve original evidence."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.test_nubank_ocr_daily_blocks import change_text, nubank_extraction
from tests.test_recovery_sweep_ocr import extraction


def mercado(*, annotated=True, mismatch=False):
    return extraction([
        ("Mercado Pago Instituição de Pagamento Ltda. EXTRATO DE CONTA", .1, .02),
        ("Periodo: De 01-08-2026 al 31-08-2026", .1, .05),
        ("Saldo inicial: R$ 100,00", .1, .08), ("Entradas: R$ 50,00", .3, .08),
        ("Saidas: R$ -20,00", .5, .08), ("Saldo final: R$ 130,00", .7, .08),
        ("DETALHE DOS MOVIMENTOS", .3, .12),
        ("Data", .1, .15), ("Descrição", .25, .15), ("ID da operação", .5, .15), ("Valor", .7, .15), ("Saldo", .85, .15),
        ("01-08-2026", .1, .25), ("Pix recebido", .25, .24), ("100001", .5, .25), ("R$ 50,00", .7, .25),
        ("R$ 151,00 0" if mismatch else "R$ 150,00 0" if annotated else "R$ 150,00", .85, .25),
        ("02-08-2026", .1, .35), ("Pagamento", .25, .34), ("100002", .5, .35), ("R$ -20,00", .7, .35),
        ("R$ $130,00" if annotated else "R$ 130,00", .85, .35),
        ("Data de geração: 01-09-2026", .1, .8), ("1/1", .9, .9),
    ])


def caixa_manager(*, opening="100,00", closing="80,00", amount="20,00", sign="- -", cropped=True):
    return extraction([
        ("GERENCIADOR CAIXA", .1, .02), ("Extrato no período de 01/08/2026 à 31/08/2026", .1, .06),
        ("Saldo anterior ao período solicitado", .1, .12), (f"R$ {opening} C", .9, .12),
        ("Data Efetiva", .05, .18), ("Valor", .7, .18), ("Saldo", .9, .18),
        ("02/08/2026", .05, .25), ("PAGAMENTO TESTE", .3, .25), (f"{sign} R$ {amount}", .7, .25),
        (f"R$ {closing} (" if cropped else f"R$ {closing} C", .9, .25),
    ])


def test_mercado_complete_ocr_grid_keeps_each_row_and_original_annotation():
    source = mercado()
    parsed = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in parsed.transactions] == [50, -20]
    assert parsed.parse_metrics["financial_controls_validated"] == 1
    assert parsed.source_extraction == source
    assert "ocr_balance_annotation_confirmed_by_controls" in parsed.canonical_transactions[0].warnings
    page = source.pages[0]
    shifted = replace(source, pages=[replace(page, lines=[replace(line, bbox={**line.bbox, "left": .84})
        if line.text in {"R$ 50,00", "R$ -20,00"} else line for line in page.lines])])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(shifted)


def test_mercado_annotation_cannot_hide_an_inconsistent_numeric_value():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(mercado(mismatch=True))


def test_caixa_duplicate_sign_and_clipped_indicator_need_the_independent_previous_balance():
    source = caixa_manager()
    parsed = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in parsed.transactions] == [-20]
    assert parsed.canonical_transactions[0].running_balance == 80
    assert "ocr_duplicate_sign_normalized" in parsed.canonical_transactions[0].warnings
    assert "balance_indicator_from_independent_controls" in parsed.canonical_transactions[0].warnings
    assert parsed.source_extraction == source


@pytest.mark.parametrize("kwargs", [{"closing": "81,00"}, {"opening": "0,00", "closing": "20,00", "sign": ""},
    {"sign": "+ -"}, {"amount": "2?,00"}])
def test_caixa_ambiguous_signs_or_unreadable_digits_are_not_filled_from_balances(kwargs):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_manager(**kwargs))


def test_nubank_column_block_reading_order_uses_summary_coordinates():
    source = nubank_extraction()
    page = source.pages[0]
    labels = {"Saldo inicial", "Saldo final do período", "Total de entradas", "Total de saídas"}
    header = page.lines[:11]
    reordered = [*header[:3], *[line for line in header[3:] if line.text in labels],
                 *[line for line in header[3:] if line.text not in labels], *page.lines[11:]]
    page = replace(page, lines=[replace(line, line_index=index) for index, line in enumerate(reordered, 1)])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[page, *source.pages[1:]]))
    assert [row.amount for row in result.transactions] == [1234.74, 1222.36, 1100, -400, -335.75]
    assert result.parse_metrics.get("statement_validation_failed", 0) == 0


def test_nubank_ocr_joined_month_year_preserves_the_printed_digits():
    source = change_text(nubank_extraction(), "11 SET 2026", "11 SET2026")
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert result.transactions[0].date == "2026-09-11"
    assert result.source_extraction == source


def test_caixa_separate_current_day_table_keeps_earlier_movements_and_proves_clipped_indicator():
    cells = [("GERENCIADOR CAIXA Extrato por período Data Mov.", .05, .02), ("Mês: Setembro/2026", .05, .05),
        ("Período: 1 - 30", .05, .08), ("Histórico", .35, .12), ("Nr. Doc.", .2, .12), ("Valor", .7, .12), ("Saldo", .9, .12),
        ("SALDO ANTERIOR", .35, .16), ("0,00 C", .7, .16), ("0,00 C", .9, .16),
        ("01/09/2026", .05, .23), ("100001", .2, .23), ("CRED PIX", .35, .23), ("100,00 C", .7, .23), ("100,00 €", .9, .23),
        ("01/09/2026", .05, .29), ("000000", .2, .29), ("SALDO DIA", .35, .29), ("0,00 C", .7, .29), ("100,00 C", .9, .29),
        ("Lançamentos do Dia", .35, .40), ("Histórico", .35, .44), ("Nr. Doc.", .2, .44), ("Valor", .7, .44), ("Saldo", .9, .44),
        ("02/09/2026", .05, .50), ("100002", .2, .50), ("PAGAMENTO TESTE", .35, .50), ("20,00 D", .7, .50), ("80,00 C", .9, .50),
        ("02/09/2026", .05, .56), ("000000", .2, .56), ("SALDO DIA", .35, .56), ("0,00 C", .7, .56), ("80,00 C", .9, .56)]
    source = extraction(cells)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [100, -20]
    assert result.parse_metrics["verified_closing_balance"] == 80
    assert result.source_extraction == source
