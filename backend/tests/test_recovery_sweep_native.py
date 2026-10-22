"""Synthetic structures only; no production identities or account data."""

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from app.application.financial_document_detection import detect_financial_report
from app.application.parsers.pdf.afternoon_statements import ALELO, parse_afternoon_statement
from app.application.parsers.pdf.verified_statements import parse_verified_statement
from app.application.pdf_layout_inference import infer_pdf_layout


def test_credisis_chronological_cash_preserves_balances_and_excludes_pending_deposit():
    page = """Data Código Descrição Valor Saldo
Saldo final R$ 150,00
02/09/2026 LIBERAÇÃO DE DEP.BLOQ R$ 50,00 R$ 150,00
01/09/2026 100001 DEPOSITO BLOQ- R$ 50,00 R$ 100,00
Saldo anterior R$ 100,00
Saldos em 30/09/2026
Período: 01/09/2026 a 30/09/2026
Página 1 / 1
Ouvidoria CREDISIS"""
    result = pdf_parser._parse_pdf_transactions_from_page_texts([page], preserve_layout_spacing=True)
    assert result.layout.layout_name == "credisis_signed_cash_controls_v1"
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 50
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["non_cash_entries_excluded"] == 1


def test_caixa_effective_date_is_description_and_cash_date_stays_in_primary_column():
    page = """Saldo anterior ao período solicitado R$ 100,00 C
Data Data Efetiva Documento Histórico Valor Saldo
04/05/2026
01/05 02:44 123456 PIX TESTE
R$ 50,00 R$ 150,00 C
04/05/2026 SALDO DIA R$ 150,00 C
SAC CAIXA"""
    result = parse_verified_statement([page])
    assert len(result.rows) == 1 and result.rows[0].transaction.date == "2026-05-04"
    assert "01/05 02:44" in result.rows[0].transaction.description
    assert result.opening == 100 and result.closing == 150


def test_bb_date_split_at_page_boundary_joins_only_the_printed_digit():
    from app.application.parsers.pdf.late_statement_variants import BB_ZERO, parse_late_statement

    pages = [
        """Extrato de Conta Corrente
Dia Lote Documento Histórico Valor
30/06/2026 Saldo Anterior 0,00 (+)
01/07/202 14175 1001 Pix Recebido 50,00 (+)""",
        """Extrato de Conta Corrente
Dia Lote Documento Histórico Valor
6 COMERCIO TESTE
00/00/0000 14175 Saldo do dia 50,00 (+)
31/07/2026 S A L D O 50,00 (+)""",
    ]
    result = parse_late_statement(pages, BB_ZERO)
    assert len(result.rows) == 1 and result.rows[0].transaction.date == "2026-07-01"
    assert (2, 3) in result.rows[0].source_line_refs
    with pytest.raises(PdfFinancialValidationError):
        parse_late_statement([pages[0], pages[1].replace("6 COMERCIO", "COMERCIO")], BB_ZERO)


def test_cresol_pending_cheque_has_no_available_cash_effect_until_release():
    from tests.fixtures.afternoon_statements import cresol_descending_pages

    pages = cresol_descending_pages()
    pages[1] = pages[1].replace(
        "03/08/2026 VENDAS CARTAO", "03/08/2026 DEPÓSITOS EM CHEQUES BLOQUEADOS - POR CAIXA + R$ 50,00\n03/08/2026 VENDAS CARTAO"
    )
    statement = parse_verified_statement(pages)
    assert statement.non_cash_entries == 1
    assert len(statement.rows) == 4
    assert statement.opening == -50 and statement.closing == -30


def test_santander_inactive_cash_table_does_not_use_investment_balance():
    from app.application.parsers.pdf.native_cash_columns import parse_santander_inactive_cash

    pages = [
        """Santander EXTRATO CONSOLIDADO INTELIGENTE agosto/2026
Saldo de Conta Corrente em 31/07 9,76
Saldo de Conta Corrente em 31/08 9,76
Saldo de Investimentos com Resgate Automático 172,16
Conta Corrente
Data Descrição Nº Documento Movimentos (R$) Saldo (R$)
Créditos Débitos
SALDO EM 31/07 9,76
SALDO EM 31/08 9,76
Saldos por Período
Extrato_PJ_A4_Inteligente
Pagina: 1/1"""
    ]
    statement = parse_santander_inactive_cash(pages)
    assert statement.rows == [] and statement.closing == statement.opening
    assert float(statement.closing) == 9.76
    assert parse_santander_inactive_cash([pages[0].replace("SALDO EM 31/08 9,76", "02/08 PIX TESTE 10,00\nSALDO EM 31/08 9,76")]) is None


def test_tribanco_inherited_dates_include_all_movements_and_exclude_investment_summary():
    page = """Lançamentos da CONTA VINCULADA (123)
Data Movimentação Tipo Documento Valor
31/08/2026SALDO ANTERIOR R$ 0,00
01/09/2026CARTAO TESTE Entrada 169 +R$ 100,00
REPASSE TESTE Entrada +R$ 50,00
TRANSF. CREDITOS CARTOES Saída -R$ 150,00
SALDO C/C R$ 0,00
Saldo CDB Conta Inteligente (+) R$ 999,00
Lançamentos Futuros
https://www2.tribancoonline.com.br/ib-web/extrato 1/1"""
    result = parse_verified_statement([page])
    assert len(result.rows) == 3
    assert [r.transaction.amount for r in result.rows] == [100, 50, -150]
    assert result.opening == result.closing == 0


def test_mercado_pago_hyphen_dates_and_spanish_period_validate_all_totals():
    page = """EXTRATO DE CONTA
Periodo: De 01-07-2026 al 31-07-2026
Saldo inicial: R$ 10,00 Entradas: R$ 50,00
Saidas: R$ -20,00 Saldo final: R$ 40,00
Data Descrição ID da operação Valor Saldo
02-07-2026 Pix recebido COMERCIO TESTE 1001 R$ 50,00 R$ 60,00
03-07-2026 Pix enviado DESTINO TESTE 1002 R$ -20,00 R$ 40,00
Mercado Pago Instituição de Pagamento Ltda.
1/1"""
    result = parse_verified_statement([page])
    assert result.opening == 10 and result.closing == 40
    assert [r.transaction.date for r in result.rows] == ["2026-07-02", "2026-07-03"]
    assert [r.external_reference_id for r in result.rows] == ["1001", "1002"]
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement([page.replace("Entradas: R$ 50,00", "Entradas: R$ 51,00")])


def test_sicredi_signed_table_ignores_future_section_and_preserves_duplicate_payments():
    page = """Cooperativa:0001
Extrato(Período de 01/09/2026 a 30/09/2026)
Data Descrição Documento Valor(R$) Saldo (R$)
SALDO ANTERIOR 100,00
01/09/2026 PIX TESTE PIX_CRED 50,00 150,00
02/09/2026 PAGAMENTO TESTE PIX_DEB -20,00 130,00
02/09/2026 PAGAMENTO TESTE PIX_DEB -20,00 110,00
Lançamentos Futuros(Próximos 30 dias)
01/10/2026 DDA - Pagar Boletos -99,00
SicrediFone 0800 000 0000"""
    result = parse_verified_statement([page])
    assert len(result.rows) == 3 and result.closing == 110
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement([page.replace("-20,00 130,00", "-21,00 130,00")])


def test_bb_sisbb_uses_explicit_cd_and_treats_daily_final_balance_as_controls():
    page = """SISBB - Sistema de Informações Banco do Brasil
Data Movim. Dep. Origem Histórico Documento Valor Saldo
30/06/2026 Saldo Anterior 100,00 C 100,00 C
01/07/2026 Pix recebido 1001 50,00 C 150,00 C
02/07/2026 Pix enviado 1002 20,00 D 130,00 C
31/07/2026 S A L D O 130,00 C
Informações adicionais"""
    result = parse_verified_statement([page])
    assert len(result.rows) == 2 and result.closing == 130
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement([page.replace("20,00 D", "21,00 D")])


def test_alelo_wrapped_amount_and_description_keep_period_balance_unset():
    pages = [
        """Extrato Cartão - Despesas Corporativas
De: 01/08/2026 Até: 31/08/2026
Data Nome Número de série Tipo de Transação Estabelecimento Ramo Valor
28/08/26
13:52:24
PORTADOR TESTE 030000000000001 Transação Compra COMERCIO TESTE
-R$ 88,15
17/08/26 PORTADOR TESTE
04:47:33 030000000000001 Transação Compra SERVICO TESTE -R$ 44,99
Saldo no dia 22/10/2026 11:41:40: R$ 43,65
ALELO - PORTAL PREPAX - CLIENTE"""
    ]
    result = parse_afternoon_statement(pages, ALELO)
    assert [r.transaction.amount for r in result.rows] == [-44.99, -88.15]
    assert "COMERCIO TESTE" in result.rows[1].transaction.description
    assert result.opening is result.closing is None


def test_bb_header_cannot_be_overridden_by_caixa_in_counterparty():
    text = """SISBB - Sistema de Informações Banco do Brasil
Extrato Conta Corrente
Data Movim. Dep. Origem Histórico Documento Valor Saldo
01/07/2026 Pix recebido CAIXA 1 10,00 C 10,00 C
02/07/2026 Pix enviado CONTA CAIXA 2 5,00 D 5,00 C
SALDO"""
    assert infer_pdf_layout(text).layout_name.startswith("banco_do_brasil_")


def test_caixa_cdb_monthly_is_investment_even_with_missing_font_glyphs():
    text = ("INFORMATIVO MENSAL CDB FLEX EMPRESARIAL/CDB CAIXA PROGR. PJ\n"
            "Data aplicação 31/07/2026\nVencimento 30/08/2030\n\x00Saldo\x00 100,00")
    assert detect_financial_report(text) == ("investment_statement", "caixa_monthly_cdb_positions")
    with pytest.raises(PdfFinancialValidationError) as error:
        pdf_parser._parse_pdf_transactions_from_page_texts([text], preserve_layout_spacing=True)
    assert error.value._parse_observability["layout_inference_name"] == "caixa_monthly_cdb_positions_v1"


def test_native_strict_failure_preserves_layout_and_text_evidence(monkeypatch):
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = [
        """Extrato Cartão - Despesas Corporativas
De: 01/08/2026 Até: 31/08/2026
28/08/26 Compra TESTE -R$ ilegível
ALELO - PORTAL PREPAX - CLIENTE"""
    ]
    raw = generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in pages))
    with pytest.raises(PdfFinancialValidationError) as error:
        pdf_parser.parse_pdf_transactions(raw)
    metrics = error.value._parse_observability
    assert metrics["native_text_detected"] == 1
    assert metrics["textract_attempted"] == 0
    assert metrics["layout_inference_name"] == ALELO
    assert error.value._ocr_source_extraction.provider == "native_pdf_text"
