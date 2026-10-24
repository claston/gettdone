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
