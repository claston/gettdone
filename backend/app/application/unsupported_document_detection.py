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
ACCOUNTS_PAYABLE_REPORT_DOCUMENT_TYPE = "accounts_payable_report"
OPEN_PAYABLES_USER_MESSAGE = (
    "Este relatório contém títulos a pagar, sem comprovação de pagamento. "
    "Envie o extrato bancário para converter movimentações realizadas."
)
NOTARY_RECEIPT_DOCUMENT_TYPE = "notary_receipt"
NOTARY_RECEIPT_USER_MESSAGE = (
    "Este arquivo é um recibo de cartório, não um extrato bancário. "
    "Envie o extrato da conta em PDF, CSV, XLSX ou OFX."
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
    if "RECIBO DE CANCELAMENTO DE PROTESTO" in header_text and "CARTORIO" in header_text:
        return NOTARY_RECEIPT_DOCUMENT_TYPE
    if _is_accounts_payable_report(normalized_lines):
        return ACCOUNTS_PAYABLE_REPORT_DOCUMENT_TYPE
    if _BILLING_REPORT_TITLE in header_text and _is_billing_report(normalized_text):
        return BILLING_REPORT_DOCUMENT_TYPE
    if _PAYMENT_REPORT_TITLE in normalized_text and _is_payment_report(normalized_text):
        return PAYMENT_REPORT_DOCUMENT_TYPE
    if _is_investment_summary(normalized_lines):
        return INVESTMENT_SUMMARY_DOCUMENT_TYPE
    return None


def _is_accounts_payable_report(lines: tuple[str, ...]) -> bool:
    if not _has_primary_payable_title(lines):
        return False
    text = " ".join(lines)
    if is_open_payables_report("\n".join(lines)):
        return True
    return all(marker in text for marker in (
        "VENCIMENTO", "PAGAMENTO", "FORNECEDOR", "VALOR TOTAL", "EM ABERTO", "SITUACAO",
    ))


def has_accounts_payable_title(text: str) -> bool:
    return _has_primary_payable_title(tuple(normalize_upper_text(line) for line in text.splitlines() if line.strip()))


def is_open_payables_report(text: str) -> bool:
    lines = tuple(normalize_upper_text(line) for line in text.splitlines() if line.strip())
    compact = " ".join(lines)
    return (_has_primary_payable_title(lines) and any(line.startswith("NOTAS A PAGAR") for line in lines[:6])
            and "FORNECEDOR" in compact and "VENCIMENTO" in compact
            and any(marker in compact for marker in ("SALDO A PAGAR", "SITUACAO: A PAGAR", "SITUACAO A PAGAR")))


def _has_primary_payable_title(lines: tuple[str, ...]) -> bool:
    title_index = next(
        (index for index, line in enumerate(lines[:6])
         if re.match(r"^(?:RELATORIO DE CONTAS A PAGAR|NOTAS A PAGAR)\b", line)),
        None,
    )
    if title_index is None:
        return False
    # A report appended to a bank statement must not reject the statement.
    if any(line.startswith("EXTRATO") for line in lines[:title_index]):
        return False
    return True


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
    if all(marker in compact_text for marker in (
        "DADOS DA CONSULTA", "INVESTIMENTOS BRADESCO", "MODALIDADE", "TOTAL APLICADO (R$)",
        "VALOR PRINCIPAL (R$)", "RESGATE BRUTO (R$)", "RESGATE LIQUIDO (R$)",
    )) and "EXTRATO DE" not in header_text:
        return True
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
