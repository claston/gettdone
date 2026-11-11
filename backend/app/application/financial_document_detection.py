"""Identify report structures without deciding whether an export is supported."""
import re

from app.application.normalization.text import normalize_upper_text

RECEIVABLES_REPORT = "receivables_report"
INVESTMENT_STATEMENT = "investment_statement"
FISCAL_SUMMARY = "fiscal_summary"


def detect_financial_report(text: str) -> tuple[str, str] | None:
    lines = [normalize_upper_text(line) for line in text.splitlines() if line.strip()]
    header = " ".join(lines[:12])
    normalized = " ".join(lines)
    if (any(line.startswith('EXTRATO DE ') for line in lines[:4])
            and all(marker in header for marker in ('SITUACAO', 'CLIENTE OU FORNECEDOR', 'DOCUMENTO', 'CATEGORIA', 'VALOR', 'SALDO'))):
        return 'cash_flow_report', 'management_ledger_status_category_columns'
    # Recognize the report before generic bank parsing, including damaged table
    # rows: an unreadable investment must never fall back to fabricated cash.
    if (any('EXTRATO' in line and 'DEPOSITO A PRAZO' in line for line in lines[:20])
            and 'SICRED' in normalized):
        return INVESTMENT_STATEMENT, 'sicredi_term_deposit_monthly'
    if any(line.startswith('EXTRATOS - INVESTIMENTOS FUNDOS - MENSAL') for line in lines[:5]):
        return INVESTMENT_STATEMENT, 'bb_monthly_fund_positions'
    if ('COMPROVANTE DE PAGAMENTO' in header and 'STONE' in normalized
            and all(marker in normalized for marker in ('MODALIDADE', 'BANDEIRA', 'IDENTIFICACAO'))):
        return 'acquirer_sales_report', 'stone_payment_receipt_rows'
    investment_title = re.search(r'^EXTRATO\s+D[\sAE\x00]*\s+CONT[A\x00]?\s+INVESTIMENTO\b', ' '.join(lines[:4]))
    if (investment_title and 'BTG' in normalized
            and all(marker in normalized for marker in ('CDB', 'QUANTIDADE', 'BRUTO', 'IR', 'IOF'))):
        return INVESTMENT_STATEMENT, 'btg_cdb_positions_and_details'
    if ('PORTFOLIO DE INVESTIMENTOS' in header and 'POSICAO DA CARTEIRA' in normalized
            and 'PROVISAO IR/IOF' in normalized):
        return INVESTMENT_STATEMENT, 'portfolio_positions_and_performance'
    if ('RELATORIO DE LANCAMENTOS EM CONTA - ANALITICO' in header
            and 'SISBR' in header and all(s in normalized for s in (
                'VENCIMENTO', 'PAGAMENTO', 'LANCAMENTO', 'VALOR NOMINAL', 'VALOR PAGO'))):
        return RECEIVABLES_REPORT, 'analytic_collection_postings'
    if "RELATORIO DE VENDAS" in header and all(marker in normalized for marker in (
        "ESTABELECIMENTO(S)", "VALOR PARCELA BRUTO", "BANDEIRA", "INDICADOR", "STATUS")):
        return "acquirer_sales_report", "sales_gross_installment_columns"
    if ("EXTRATO DE CUSTODIA MENSAL/PERIODO" in header and "AGORA CORRETORA" in normalized
            and "PROVENTOS EM DINHEIRO" in normalized):
        return INVESTMENT_STATEMENT, "agora_custody_monthly_positions"
    if "FCBR76 - EXTRATO DE APLICACOES PARA SIMPLES CONFERENCIA" in header and "APLICACOES EM CDB/RDB" in header:
        return INVESTMENT_STATEMENT, "brb_fcbr76_positions"
    if ("SICOOB" in header and "SISBR SALDO RENDA FIXA" in header
            and all(marker in normalized for marker in ("VALOR APLICADO", "VALOR BRUTO", "VALOR LIQUIDO"))):
        return INVESTMENT_STATEMENT, "sicoob_sisbr_fixed_income_positions"
    if all(marker in normalized for marker in (
        "FINANCAS - MOVIMENTACAO DA CONTA CORRENTE", "SITUACAO", "SALDO PREVISTO (R$)", "OMIE.CASH")):
        return "cash_flow_report", "omie_cash_actual_forecast_columns"
    if ("XP INVESTIMENTOS" in header and "EXTRATO DA CONTA" in header
            and all(marker in normalized for marker in ("DATA DA CONSULTA:", "XPI.COM.BR", "LANCAMENTOS FUTUROS"))
            and re.search(r"\bLIQ\s+MOV\s+HISTORICO\s+VALOR\s+SALDO\b", normalized)):
        return INVESTMENT_STATEMENT, "xp_account_web_ledger"
    if ("EXTRATO DA CONTA INVESTIMENTO" in header and "XP INVESTIMENTOS" in normalized
            and "XPI.COM.BR" in normalized and "TRANSACOES NO PERIODO" in normalized
            and "LIQUIDACAO" in normalized):
        return INVESTMENT_STATEMENT, "xp_investment_account_ledger"
    if "RELATORIO DE VENDAS" in header and all(
        marker in normalized for marker in ("ADQUIRENTE", "STATUS", "VALOR BRUTO", "TAXA", "VALOR LIQUIDO", "PAGBANK")
    ):
        return RECEIVABLES_REPORT, "pagbank_sales_gross_fee_net_status"
    if "INFORMATIVO MENSAL CDB" in header and "CDB CAIXA" in header:
        return INVESTMENT_STATEMENT, "caixa_monthly_cdb_positions"
    if ("BTG PACTUAL" in header and "INVESTIMENTO" in header and "EXTRATO" in header
            and "CONTA CORRENTE - MOVIMENTACAO" in normalized
            and re.search(r"DISTRIBUICAO DA CARTEIRA\s+CONTA CORRENTE\s+100[.,]00%", normalized)):
        return INVESTMENT_STATEMENT, "btg_investment_cash_ledger"
    if ("BTG PACTUAL" in header and "INVESTIMENTO" in header
            and "FUNDO DE INVESTIMENTO" in normalized and "EXTRATO" in header):
        return INVESTMENT_STATEMENT, "btg_fund_positions_and_lots"
    if "SICOOB" in header and "CAPTACAO REMUNERADA" in header and "EXTRATO CONSOLIDADO" in header and "REM116" in normalized:
        return INVESTMENT_STATEMENT, "sicoob_rem116_period_summary"
    # A counterparty or appendix cannot change the identity of the first document.
    if any(re.match(r"^EXTRATO (?:DE )?(?:CONTA|BANCARIO|MENSAL)", line) for line in lines[:6]):
        return None
    if "EXTRATO DE RENDIMENTOS" in header and "CAIXINHAS PJ" in header:
        return INVESTMENT_STATEMENT, "nubank_rdb_caixinhas_columns"
    if (
        "TITULOS PAGOS POR CONTA CREDITO" in header
        and all(marker in normalized for marker in ("PAGADOR", "VALOR DO TITULO", "VALOR COBRADO", "DT DE PAG"))
    ):
        return RECEIVABLES_REPORT, "paid_collection_titles_columns"
    if (
        "EXTRATO DE INVESTIMENTOS" in header and "CDB" in header
        and all(marker in normalized for marker in ("VLR PRINC", "IOF", "IRRF", "RESGATES", "APLICACOES"))
    ):
        return INVESTMENT_STATEMENT, "cdb_movements_and_positions_columns"
    if (
        "NF-E, NFC-E E SAT (RESUMO)" in header
        and all(marker in normalized for marker in ("EMISSAO", "CHAVE", "PROTOCOLO", "VALOR"))
    ):
        return FISCAL_SUMMARY, "fiscal_documents_summary_columns"
    return None
