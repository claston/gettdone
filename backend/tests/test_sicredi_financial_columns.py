"""Sicredi native columns and daily totals; synthetic sources only."""
import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext


def matrix_text():
    def row(day, document, description, debit="", credit="", balance=""):
        return f"{day:<12}{document:<12}{description:<49}{debit:>13}{credit:>15}{balance:>16}"
    return "\n".join([
        "CC ALIANCA EXTRATO DE CONTA CORRENTE", "PERIODO: DE 09/2026 A 09/2026",
        row("DATA", "DOCUMENTO", "HISTORICO", "DEBITO", "CREDITO", "SALDO"),
        row("**/**/****", "*********", "S A L D O A N T E R I O R", balance="3.982,13"),
        row("01/09/2026", "PIXCOBRAN", "RECEB. COB HIBRIDA CRED_COBPIX", credit="2.600,00"),
        row("01/09/2026", "CAPTACAO", "APLIC.FINANC.AVISO PREVIO", debit="250,00"),
        row("01/09/2026", "TAR001", "JUROS UTILIZ.CH.ESPECIAL", debit="11,61", balance="6.320,52"),
        row("02/09/2026", "", "CHEQUE COMPE SICREDI", debit="7.303,30", balance="-982,78"),
        "SALDO ATUAL......: -982,78 -982,78", "DEBITOS.............: 7.564,91", "CREDITOS............: 2.600,00",
    ])


def html_text():
    return "\n".join([
        "EMPRESA EXEMPLO", "Extrato Sicredi do período de 01/09/2026 a 30/09/2026",
        "Agência / Conta", "748-0001-00000001-0", "Saldo Inicial (R$): R$ 93,34", "Saldo Final (R$): R$ 13,40",
        "Data Lançamento Documento Crédito (R$) Débito (R$) Saldo (R$)",
        "01/09/2026 IOF BASICO CH PJ -R$ 0,01", "01/09/2026 IOF ADICIONAL PJ-CH. ESPE -R$ 1,46",
        "Total do dia -R$ 1,47 R$ 91,87", "02/09/2026 JUROS UTILIZ.CH.ESPECIAL -R$ 1,15",
        "Total do dia -R$ 1,15 R$ 90,72", "08/09/2026", "LIQUIDACAO BOLETO FORNECEDOR EXEMPLO",
        "28.941.534/0001-31 -R$ 417,42", "08/09/2026 RECEBIMENTO PIX CLIENTE EXEMPLO", "R$ 600,00",
        "08/09/2026", "LIQUIDACAO BOLETO OUTRO FORNECEDOR", "18.686.876/0001-07 -R$ 180,00",
        "Total do dia R$ 600,00 -R$ 597,42 R$ 93,30", "10/09/2026 CESTA DE RELACIONAMENTO -R$ 69,90",
        "Total do dia -R$ 69,90 R$ 23,40", "14/09/2026 DEB.CTA.FATURA", "SICREDI EXEMPLO -R$ 10,00",
        "Total do dia -R$ 10,00 R$ 13,40", "Total R$ 600,00 -R$ 679,94 R$ 13,40",
    ])


def parse(text):
    return pdf_parser._parse_pdf_transactions_from_page_texts([text], preserve_layout_spacing=True,
        context=LayoutSpecificParseContext(is_native_text=True))


def test_matrix_column_roles_override_description_hints_and_validate_summary():
    result = parse(matrix_text())
    assert [row.amount for row in result.transactions] == [2600, -250, -11.61, -7303.3]
    assert result.canonical_transactions[-1].running_balance == -982.78
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["selected_parser"] == "layout_specific_sicredi_matrix"


def test_html_wrapped_movements_keep_dates_and_daily_totals_are_not_transactions():
    result = parse(html_text())
    assert len(result.transactions) == 8
    assert [row.amount for row in result.transactions] == [-.01, -1.46, -1.15, -417.42, 600, -180, -69.9, -10]
    assert [row.date for row in result.transactions[3:6]] == ["2026-09-08"] * 3
    assert all(not row.description.startswith("Total") for row in result.transactions)
    assert "LIQUIDACAO BOLETO" in result.transactions[3].description
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["selected_parser"] == "layout_specific_sicredi_html"


@pytest.mark.parametrize("source,before,after", [
    (matrix_text, "7.564,91", "7.564,92"),
    (matrix_text, "6.320,52", "6.320,53"),
    (matrix_text, "3.982,13", "3.982,14"),
    (matrix_text, "DE 09/2026 A", "DE 00/2026 A"),
    (html_text, "-R$ 417,42", "-R$ 417,43"),
    (html_text, "R$ 600,00 -R$ 679,94", "R$ 600,00 -R$ 679,95"),
    (html_text, "R$ 91,87", "R$ 91,88"),
])
def test_inconsistent_summary_or_daily_controls_require_review(source, before, after):
    with pytest.raises(PdfFinancialValidationError):
        parse(source().replace(before, after))


def test_negative_printed_amount_in_credit_column_is_not_made_positive():
    with pytest.raises(PdfFinancialValidationError):
        parse(matrix_text().replace("2.600,00", "-2.600,00", 1))


def test_invalid_transaction_date_cannot_be_silently_dropped():
    with pytest.raises(PdfFinancialValidationError):
        parse(html_text().replace("02/09/2026", "32/09/2026"))
