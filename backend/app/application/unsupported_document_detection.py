from __future__ import annotations

import re

from app.application.normalization.text import normalize_upper_text

BILLING_REPORT_DOCUMENT_TYPE = "billing_report"
BILLING_REPORT_USER_MESSAGE = (
    "Este arquivo é um relatório de faturamento, não um extrato bancário. "
    "Envie o extrato da conta em PDF, CSV, XLSX ou OFX."
)
PAYMENT_REPORT_DOCUMENT_TYPE = "payment_report"
PAYMENT_REPORT_USER_MESSAGE = (
    "Este arquivo é uma lista de créditos ou pagamentos, não é um extrato bancário. "
    "Envie o extrato da conta em PDF, CSV, XLSX ou OFX."
)
INVESTMENT_SUMMARY_DOCUMENT_TYPE = "investment_summary"
INVESTMENT_SUMMARY_USER_MESSAGE = (
    "Este arquivo é um resumo ou posição de investimentos, sem lançamentos compatíveis com esta conversão. "
    "Envie um extrato com as movimentações da conta."
)

_BILLING_REPORT_TITLE = "RELATORIO DE FATURAMENTO"
_MONTH_NAMES = (
    "JANEIRO",
    "FEVEREIRO",
    "MARCO",
    "ABRIL",
    "MAIO",
    "JUNHO",
    "JULHO",
    "AGOSTO",
    "SETEMBRO",
    "OUTUBRO",
    "NOVEMBRO",
    "DEZEMBRO",
)
_BILLING_REPORT_SUPPORTING_MARKERS = (
    "PERIODO",
    "TOTAIS",
    "CNPJ",
    "REGISTRO NO C.R.C",
    "ALIQUOTA",
    "IMPOSTOS",
)
_PAYMENT_REPORT_TITLE = "LISTA DE CREDITOS"
_PAYMENT_REPORT_SUPPORTING_MARKERS = (
    "NUMERO DO PAGAME",
    "NUMERO DO CLIENTE",
    "FUNCIONARIO",
    "DATA DE PAGAMENT",
    "TIPO DE PAGAMENTO",
    "SITUACAO",
    "TOTAL COMPROMISSOS",
)
_DATED_ROW_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s+|$)(?P<description>.*)")
_CASH_MOVEMENT_PATTERN = re.compile(
    r"^(?:APLICACAO|RESGATE|RENDIMENTO MENSAL|PIX|TED|SISPAG|SAQUE|DEPOSITO|PAGAMENTO|TRANSFERENCIA)\b"
)


def detect_unsupported_document_type(text: str) -> str | None:
    normalized_lines = tuple(
        normalized
        for raw_line in str(text or "").splitlines()
        if (normalized := normalize_upper_text(raw_line).strip())
    )
    if not normalized_lines:
        return None

    normalized_text = "\n".join(normalized_lines)
    header_text = " ".join(normalized_lines[:6])
    if _BILLING_REPORT_TITLE in header_text and _is_billing_report(normalized_text):
        return BILLING_REPORT_DOCUMENT_TYPE
    if _PAYMENT_REPORT_TITLE in normalized_text and _is_payment_report(normalized_text):
        return PAYMENT_REPORT_DOCUMENT_TYPE
    if _is_investment_summary(normalized_lines):
        return INVESTMENT_SUMMARY_DOCUMENT_TYPE
    return None


def _is_billing_report(normalized_text: str) -> bool:
    month_hits = sum(
        re.search(rf"(?<![A-Z0-9]){month}(?![A-Z0-9])", normalized_text) is not None
        for month in _MONTH_NAMES
    )
    supporting_hits = sum(marker in normalized_text for marker in _BILLING_REPORT_SUPPORTING_MARKERS)
    return month_hits >= 6 and supporting_hits >= 2


def _is_payment_report(normalized_text: str) -> bool:
    supporting_hits = sum(marker in normalized_text for marker in _PAYMENT_REPORT_SUPPORTING_MARKERS)
    return supporting_hits >= 4


def _is_investment_summary(lines: tuple[str, ...]) -> bool:
    # Detailed statements can contain investment summaries as appendices.
    # An account-statement header or dated cash movement keeps the existing path.
    header_text = " ".join(lines[:6])
    if "CONTA CORRENTE" in header_text:
        return False
    for index, line in enumerate(lines):
        match = _DATED_ROW_PATTERN.match(line)
        if match is None:
            continue
        description = match.group("description") or (lines[index + 1] if index + 1 < len(lines) else "")
        if _CASH_MOVEMENT_PATTERN.match(description):
            return False

    compact_text = " ".join(lines)
    if (
        "EXTRATO" in header_text
        and re.search(r"\bCDB\b", header_text)
        and "RESUMO DO PERIODO" in compact_text
        and any(line.startswith("POSICAO EM ") for line in lines)
        and "VALOR APLICACAO" in compact_text
        and "REMUNERACAO" in compact_text
    ):
        return True

    summary_rows = {
        match.group(1)
        for line in lines
        if (match := re.match(r"^(PRINCIPAL|BRUTO|LIQUIDO)(?:\s|$)", line))
    }
    return {"PRINCIPAL", "BRUTO", "LIQUIDO"}.issubset(summary_rows) and all(
        marker in compact_text
        for marker in ("SALDO EM", "APLICACOES", "RESGATES", "VENCIMENTOS", "RENDIMENTO APURADO")
    )
