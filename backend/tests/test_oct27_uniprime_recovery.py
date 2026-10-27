"""Fictional Uniprime ledger followed by a position/interest appendix."""
import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf


def uniprime_with_position(*, mutation=None):
    def columns():
        return [("Data", .04, .12), ("Nº Doc", .14, .12), ("Histórico", .25, .12),
                ("Valor (R$)", .73, .12), ("Saldo (R$)", .87, .12)]

    pages = [[("18/12/2020", .04, .02), ("Uniprime Cooperativa: FICTÍCIA", .04, .05),
              ("Extrato de 01/01/2020 à 31/01/2020", .04, .08), *columns(),
              ("Saldo Anterior (R$)", .25, .18), ("100,00", .87, .18),
              ("02/01/2020", .04, .25), ("000001", .14, .25), ("DÉBITO FICTÍCIO", .25, .25),
              ("30,00 -", .73, .25), ("70,00", .87, .25)],
             [("18/12/2020", .04, .02), *columns(),
              ("03/01/2020", .04, .25), ("000002", .14, .25), ("CRÉDITO FICTÍCIO", .25, .25),
              ("50,00 +", .73, .25), ("120,00", .87, .25),
              ("Consulta Posição Consolidada", .04, .55),
              ("Saldo em 18/12/2020", .04, .62), ("R$ 0,10", .90, .62),
              ("Valor de Juros Mensal do Cheque Especial", .04, .68), ("R$ 0,00", .90, .68),
              ("Data de Vencimento do Cheque Especial", .04, .74), ("18/12/2020", .87, .74),
              ("Informação do Cheque Especial", .04, .80)],
             [("18/12/2020", .04, .02), ("CET - Custo Efetivo Total (m/a)", .04, .08), ("-1,00%", .90, .08),
              ("IOF Adicional", .04, .14), ("0,38%", .90, .14),
              ("Extrato para simples conferência em 18/12/2020 às 15:05", .25, .25)]]
    if mutation == "missing_section_boundary":
        pages[1] = [cell for cell in pages[1] if cell[0] != "Consulta Posição Consolidada"]
    if mutation == "cash_after_boundary":
        pages[1] += [("04/01/2020", .04, .90), ("MOVIMENTO INESPERADO", .25, .90),
                     ("10,00 +", .73, .90), ("130,00", .87, .90)]
    if mutation == "cash_on_appendix_page":
        pages[2] += [("04/01/2020", .04, .40), ("MOVIMENTO INESPERADO", .25, .40),
                     ("10,00 +", .73, .40), ("130,00", .87, .40)]
    if mutation == "second_table":
        pages[2] += columns()
    if mutation == "heading_as_description":
        pages[0] = [("Consulta Posição Consolidada" if text == "DÉBITO FICTÍCIO" else text, x, y)
                    for text, x, y in pages[0]]
    if mutation == "real_date_outside_period":
        pages[1] = [("03/12/2020" if text == "03/01/2020" else text, x, y) for text, x, y in pages[1]]
    if mutation == "missing_row_date":
        pages[0] = [cell for cell in pages[0] if cell[0] != "02/01/2020"]
    if mutation == "balance_mismatch":
        pages[1] = [("121,00" if text == "120,00" else text, x, y) for text, x, y in pages[1]]
    if mutation == "centered_date_heading":
        pages = [[(text, .10 if text == "Data" else x, y) for text, x, y in page] for page in pages]
    return grid_pdf(pages)


def test_uniprime_print_dates_and_interest_appendix_are_not_movements(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(uniprime_with_position())
    assert [(row.date, row.amount) for row in result.transactions] == [("2020-01-02", -30), ("2020-01-03", 50)]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.parse_metrics["printed_balance_checks"] == 2
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions for row in result.canonical_transactions)
    assert not any(row.warnings for row in result.canonical_transactions)
    assert len(result.source_page_texts) == 3
    assert result.parse_metrics["textract_attempted"] == 0


@pytest.mark.parametrize("mutation", ["cash_after_boundary", "cash_on_appendix_page", "second_table",
                                      "missing_section_boundary", "real_date_outside_period", "missing_row_date"])
def test_uniprime_boundary_does_not_hide_unexplained_movement_data(mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(uniprime_with_position(mutation=mutation))


def test_uniprime_section_title_in_a_description_does_not_end_the_ledger():
    result = pdf_parser.parse_pdf_transactions(uniprime_with_position(mutation="heading_as_description"))
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "Consulta Posição Consolidada"


def test_uniprime_position_appendix_does_not_suppress_real_balance_difference():
    result = pdf_parser.parse_pdf_transactions(uniprime_with_position(mutation="balance_mismatch"))
    assert [row.amount for row in result.transactions] == [-30, 50]
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


def test_uniprime_shared_pipeline_uses_the_statement_balance_not_the_later_position():
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=uniprime_with_position(),
        analysis_id="an_uniprime_position_fictional").analysis_data
    assert analysis.transactions_total == 2
    assert analysis.opening_balance == 100
    assert analysis.closing_balance == 120
    assert analysis.bank_code == "099"


def test_uniprime_centered_date_heading_does_not_discard_document_references():
    result = pdf_parser.parse_pdf_transactions(uniprime_with_position(mutation="centered_date_heading"))
    assert [row.external_reference_id for row in result.canonical_transactions] == ["000001", "000002"]
