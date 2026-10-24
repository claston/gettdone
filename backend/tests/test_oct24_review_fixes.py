"""Synthetic regressions for invoice subtotals and truncated bank exports."""

from decimal import Decimal

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import _extract_bank_account_metadata, build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.pdf_layout_inference import infer_pdf_layout
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

NUBANK_CARD = "nubank_fatura_cartao_credito_v1"


def invoice_pages():
    return ["""Nubank
FATURA 13 OUT 2026 EMISSÃO E ENVIO 04 OUT 2026
RESUMO DA FATURA ATUAL
Fatura anterior R$ 150,00
Pagamento recebido −R$ 150,00
Total de compras de todos os cartões, 04 SET a 04 OUT R$ 220,00
Outros lançamentos −R$ 20,00
Total a pagar R$ 200,00
PRÓXIMAS FATURAS
Fechamento da próxima fatura 04 NOV 2026
1 de 3""", """Nubank
FATURA 13 OUT 2026 EMISSÃO E ENVIO 04 OUT 2026
TRANSAÇÕES DE 04 SET A 04 OUT
CLIENTE FICTÍCIO R$ 200,00
04 SET
 •••• 1234 LOJA A - Parcela 2/2 R$ 120,00
09 SET
 •••• 5678 LOJA B R$ 100,00
2 de 3""", """Nubank
FATURA 13 OUT 2026 EMISSÃO E ENVIO 04 OUT 2026
TRANSAÇÕES DE 04 SET A 04 OUT
03 OUT
 •••• 1234 ESTORNO LOJA A −R$ 20,00
Pagamentos -R$ 150,00
09 SET Pagamento em 09 SET −R$ 100,00
09 SET Pagamento em 09 SET −R$ 50,00
Em cumprimento à regulação do Banco Central, consulte o SCR.
3 de 3"""]


def santander_page(*, notice=True):
    return """Santander
Aplicativo Santander Empresas
Extrato de conta corrente
Agência: 1234 Conta: 123456789
Períodos: 01/09/2026 a 30/09/2026
Data Histórico Documento Valor (R$) Saldo (R$)
30/09/2026 PIX RECEBIDO 100,00 1.200,00
18/09/2026 PIX RECEBIDO 50,00 1.100,00
""" + ("Você alcançou o número máximo de consultas. Consulte pelo Internet Banking Empresarial, em Extrato Consolidado\n"
       "para ver todos os lançamentos.\n" if notice else "")


def invoice_pdf(pages=None):
    # The synthetic PDF font is Latin-1; text-level cases also exercise Unicode minus.
    return generate_native_text_pdf(tuple(page.replace("−", "-").replace("•", "*").splitlines() for page in (pages or invoice_pages())))


def test_nubank_invoice_has_its_own_layout_and_excludes_subtotals():
    pages = invoice_pages()
    assert infer_pdf_layout("\n".join(pages)).layout_name == NUBANK_CARD
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    assert result.layout.layout_name == NUBANK_CARD
    assert [row.amount for row in result.transactions] == [-120, -100, 20, 100, 50]
    assert [row.date for row in result.transactions] == ["2026-09-04", "2026-09-09", "2026-10-03", "2026-09-09", "2026-09-09"]
    assert all(row.description != "PAGAMENTOS" for row in result.transactions)
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_opening_balance"] == -150
    assert result.parse_metrics["verified_closing_balance"] == -200
    assert result.parse_metrics["statement_rows_expected"] == 5
    assert result.parse_metrics["export_recommendation"] == "safe_to_export"


def test_nubank_native_invoice_pipeline_preserves_credit_card_signs_and_balances(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = invoice_pdf()
    data = build_default_conversion_pipeline().run(filename="fatura.pdf", raw_bytes=raw, analysis_id="an_nubank_card").analysis_data
    assert data.ofx_account_type == "credit_card"
    assert data.bank_name == "Nubank"
    assert data.opening_balance == -150
    assert data.closing_balance == -200
    assert data.total_inflows == 170
    assert data.total_outflows == -220


@pytest.mark.parametrize("before,after", [
    ("LOJA B R$ 100,00", "LOJA B R$ 101,00"),
    ("Pagamento em 09 SET −R$ 50,00", "Pagamento em 09 SET −R$ 51,00"),
    ("09 SET Pagamento em 09 SET −R$ 50,00", ""),
])
def test_nubank_strict_reader_validates_purchases_and_payments_independently(before, after):
    pages = [page.replace(before, after) for page in invoice_pages()]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts(pages)


def test_nubank_invoice_year_comes_from_billing_date():
    pages = [page.replace("13 OUT 2026", "13 JAN 2027").replace("04 OUT 2026", "04 JAN 2027")
             .replace("04 SET", "04 DEZ").replace("09 SET", "09 DEZ").replace("03 OUT", "03 JAN") for page in invoice_pages()]
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.date for row in result.transactions] == ["2026-12-04", "2026-12-09", "2027-01-03", "2026-12-09", "2026-12-09"]
    assert sum(Decimal(str(row.amount)) for row in result.transactions) == -50


def test_nubank_account_statement_keeps_existing_layout():
    text = "Nubank\nConta do Nubank\nMovimentações\nTotal de entradas R$ 50,00\nTotal de saídas R$ 20,00\nSaldo do dia R$ 30,00"
    assert infer_pdf_layout(text).layout_name == "nubank_statement_ptbr"


@pytest.mark.parametrize("notice", [
    "Você alcançou o número máximo de consultas. Consulte pelo Internet Banking Empresarial, "
    "em Extrato Consolidado para ver todos os lançamentos.",
    "VOCE ALCANCOU O NUMERO MAXIMO DE CONSULTAS.\nCONSULTE PELO INTERNET BANKING EMPRESARIAL, "
    "EM EXTRATO CONSOLIDADO\nPARA VER TODOS OS LANCAMENTOS.",
])
def test_bank_query_limit_is_partial_even_with_all_physical_pages(notice):
    assert validate_statement_completeness([santander_page(notice=False) + notice + "\nPágina: 1 de 1"])


def test_santander_query_limit_preserves_readable_rows_with_explicit_warning(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((santander_page().splitlines(),))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in result.transactions] == [100, 50]
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["export_recommendation_reason"] == "partial_statement"
    assert "partial_statement" in result.canonical_transactions[0].warnings


def test_transaction_mentions_of_query_limits_do_not_mark_statement_partial():
    assert not validate_statement_completeness([santander_page(notice=False) + "TARIFA CONSULTAS 10,00"])


@pytest.mark.parametrize("account", ["123456789", "00012345678901234567", "0001234567890123456-8"])
@pytest.mark.parametrize("separator", [": ", "\n"])
def test_account_metadata_preserves_up_to_twenty_digits(account, separator):
    assert _extract_bank_account_metadata(f"Santander\nAgência: 1234\nConta{separator}{account}") == ("1234", account)
