"""Bank structures and fictional entries for the last recovery sweep."""
import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError


def parse(text):
    return pdf_parser._parse_pdf_transactions_from_page_texts([text], preserve_layout_spacing=True)


def english_statement():
    return """Bank Statement
Account Type CHECKING
Currency BRL
Bank ID 001
Period 09/28/2026 - 10/15/2026
Current Balance R$0.00
Total Credits R$1,200.00
Total Debits R$1,200.00
Transaction Details (2 transactions)
Date Type Description Memo Amount
10/01/2026 DEP COBRANÇA R$1,200.00
10/13/2026 DEBIT PAGAMENTO CAIXA -R$1,200.00
TOTAL +R$1,200.00 -R$1,200.00
Page 1"""


def test_bb_english_preserves_all_rows_october_dates_us_amounts_and_identity():
    result = parse(english_statement())
    assert result.layout.layout_name == "bb_english_bank_statement_v1"
    assert [r.date for r in result.transactions] == ["2026-10-01", "2026-10-13"]
    assert [r.amount for r in result.transactions] == [1200, -1200]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("old,new", [("2 transactions", "3 transactions"), ("-R$1,200.00\nTOTAL", "-R$1,100.00\nTOTAL"),
                                     ("10/13/2026", "10/32/2026"), ("10/13/2026", "11/13/2026")])
def test_bb_english_rejects_count_totals_invalid_and_out_of_period_dates(old, new):
    with pytest.raises(PdfFinancialValidationError):
        parse(english_statement().replace(old, new))


def test_itau_native_positions_are_not_transactions_or_cash_checkpoints():
    result = parse("""ItaúEmpresas
Extrato de 01/10/2026 até 31/10/2026
Data Lançamento Ag./Origem Valor (R$) Saldo (R$)
30/09 SALDO ANTERIOR 1,00
01/10 PIX ENVIADO TESTE 9001 -50,00
01/10 REDE VISA TESTE 9002 100,00
01/10 SDO CTA/APL AUTOMATICAS 9.000,00
31/10 S A L D O 1,00""")
    assert len(result.transactions) == 2
    assert [r.amount for r in result.transactions] == [-50, 100]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def brb_page():
    return """Extrato
AGOSTO/2026
CRÉDITO PIX – DOC: 100001
DÉBITO PIX – DOC: 100002
Agência: 100 — Conta Corrente: 100.001.002-3
Dia Histórico Valor
27/08 COMÉRCIO TESTE — 0105 R$ +100,00
26/08 TARIFA TESTE — 0105 R$ −10,00
SALDO ATUAL
Saldo Conta Corrente R$ +90,00
Saldo da Poupança / CDB Automático / Fundo Automático (+) R$ 0,00
Saldo Atual (=) R$ +90,00"""


def test_brb_native_pairs_displaced_history_with_date_value_and_unicode_sign():
    result = parse(brb_page())
    assert result.layout.layout_name == "brb_monthly_displaced_history_v1"
    assert len(result.transactions) == 2
    assert "DÉBITO PIX" in result.transactions[0].description
    assert "TARIFA TESTE" in result.transactions[0].description
    assert [r.amount for r in result.transactions] == [-10, 100]
    assert result.parse_metrics["financial_controls_validated"] == 0


def test_brb_never_silently_loses_displaced_history():
    with pytest.raises(PdfFinancialValidationError):
        parse(brb_page().replace("DÉBITO PIX – DOC: 100002\n", ""))
