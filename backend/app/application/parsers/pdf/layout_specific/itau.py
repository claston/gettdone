from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.date import STATEMENT_DATE_TOKEN
from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    find_amount_tokens,
    has_explicit_amount_sign,
    parse_pdf_amount,
)
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import (
    build_parsed_transaction,
    infer_default_statement_year_from_lines,
    normalize_text,
)
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

ITAU_LANCAMENTOS_LAYOUT = "itau_empresas_extrato_lancamentos_conta_corrente_v1"
ITAU_MONTHLY_LAYOUT = "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1"
ITAU_30_HOURS_LAYOUT = "itau_empresas_extrato_30_horas_tabela_v1"
ITAU_HISTORY_LAYOUT = "itau_extrato_historico_lancamentos_orig_valor_saldo_v1"
ITAU_CARDS_LAYOUT = "itau_empresas_extrato_completo_cards_v1"
ITAU_POSITION_LAYOUT = "itau_empresas_extrato_30_horas_posicao_conta_corrente_v1"
ITAU_COLLECTION_LAYOUT = "itau_empresas_cobranca_movimentacao_detalhada_v1"
ITAU_PAYMENTS_LAYOUT = "itau_empresas_consulta_pagamentos_transferencias_pix_v1"
ITAU_SAVINGS_LAYOUT = "itau_extrato_poupanca_entradas_saidas_v1"
ITAU_RECEIVED_TRANSFERS_LAYOUT = "itau_empresas_transferencias_recebidas_v1"
ITAU_COMPLETE_TABLE_LAYOUT = "itau_empresas_extrato_completo_tabela_v1"
ITAU_TRANSFER_RECEIPT_LAYOUT = "itau_comprovante_transferencia_pix_v1"

_STANDARD_DATE_FIRST_LAYOUTS = frozenset(
    {
        ITAU_LANCAMENTOS_LAYOUT,
        ITAU_MONTHLY_LAYOUT,
        ITAU_30_HOURS_LAYOUT,
        ITAU_HISTORY_LAYOUT,
        ITAU_CARDS_LAYOUT,
        ITAU_POSITION_LAYOUT,
        ITAU_COMPLETE_TABLE_LAYOUT,
    }
)
_ALL_LAYOUTS = frozenset(
    {
        *_STANDARD_DATE_FIRST_LAYOUTS,
        ITAU_COLLECTION_LAYOUT,
        ITAU_PAYMENTS_LAYOUT,
        ITAU_SAVINGS_LAYOUT,
        ITAU_RECEIVED_TRANSFERS_LAYOUT,
        ITAU_TRANSFER_RECEIPT_LAYOUT,
    }
)
_DATE_AT_START_PATTERN = re.compile(
    rf"^\s*(?P<date>{STATEMENT_DATE_TOKEN})(?=\s|$)(?P<rest>.*)$",
    flags=re.IGNORECASE,
)
_DATE_PATTERN = re.compile(rf"(?<!\d)(?:{STATEMENT_DATE_TOKEN})(?!\d)", flags=re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2}|2100)\b")
_MONTH_YEAR_PATTERN = re.compile(
    r"\b(?:JAN(?:EIRO)?|FEV(?:EREIRO)?|MAR(?:CO)?|ABR(?:IL)?|MAI(?:O)?|JUN(?:HO)?|"
    r"JUL(?:HO)?|AGO(?:STO)?|SET(?:EMBRO)?|OUT(?:UBRO)?|NOV(?:EMBRO)?|DEZ(?:EMBRO)?)"
    r"\s*[/ -]\s*(20\d{2})\b"
)
_SAVINGS_AMOUNT_PATTERN = re.compile(
    r"(?<![\d,.])(?P<amount>\(?(?:[+\-]\s*)?(?:R\$\s*)?"
    r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[+\-]?\)?)(?![\d,.])",
    flags=re.IGNORECASE,
)
_BALANCE_DESCRIPTIONS = (
    "SALDO ANTERIOR",
    "SALDO INICIAL",
    "SALDO DO DIA",
    "SALDO FINAL",
    "SALDO TOTAL",
    "SALDO APLIC",
)


@dataclass(frozen=True, slots=True)
class ItauLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name not in self.layout_names:
            return None

        fallback_year = _infer_itau_year(lines)
        if fallback_year is None and context.reference_month_year is not None:
            fallback_year = context.reference_month_year[1]

        if layout_name in _STANDARD_DATE_FIRST_LAYOUTS:
            rows = _parse_date_first_rows(lines, fallback_year=fallback_year)
        elif layout_name == ITAU_SAVINGS_LAYOUT:
            rows = _parse_savings_rows(lines, fallback_year=fallback_year)
        elif layout_name == ITAU_COLLECTION_LAYOUT:
            rows = _parse_collection_rows(lines, fallback_year=fallback_year)
        elif layout_name == ITAU_PAYMENTS_LAYOUT:
            rows = _parse_payment_rows(lines, fallback_year=fallback_year)
        elif layout_name == ITAU_RECEIVED_TRANSFERS_LAYOUT:
            rows = _parse_received_transfer_rows(lines, fallback_year=fallback_year)
        else:
            rows = _parse_transfer_receipt(lines, fallback_year=fallback_year)

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_itau",
            selection_reason=f"layout_specific_itau:{layout_name}",
        )


def _parse_date_first_rows(lines: list[_PdfLine], *, fallback_year: int | None) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        rest = match.group("rest").strip()
        if _is_balance_or_summary_row(rest):
            continue
        amount_tokens = find_amount_tokens(rest)
        if not amount_tokens:
            continue
        date = _parse_date(match.group("date"), fallback_year=fallback_year)
        if date is None:
            continue
        amount_token = amount_tokens[0]
        amount = parse_pdf_amount(amount_token.value)
        description = _clean_description(rest, amount_tokens=(amount_token,))
        if not description:
            continue
        rows.append(_build_row(date=date, description=description, amount=amount, amount_token=amount_token, source=line))
    return rows


def _parse_savings_rows(lines: list[_PdfLine], *, fallback_year: int | None) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        rest = match.group("rest").strip()
        if _is_balance_or_summary_row(rest):
            continue
        tokens = _find_savings_amount_tokens(rest)
        selected = next((token for token in tokens if abs(parse_pdf_amount(token.value)) > 0.00001), None)
        if selected is None:
            continue
        date = _parse_date(match.group("date"), fallback_year=fallback_year)
        if date is None:
            continue
        description = _clean_description(rest, amount_tokens=tuple(tokens))
        if not description:
            continue
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=parse_pdf_amount(selected.value),
                amount_token=selected,
                source=line,
            )
        )
    return rows


def _parse_collection_rows(lines: list[_PdfLine], *, fallback_year: int | None) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        date_matches = list(_DATE_PATTERN.finditer(line.text))
        amount_tokens = find_amount_tokens(line.text)
        if "BOLETO" not in normalized or len(date_matches) < 2 or not amount_tokens:
            continue
        movement_date_match = date_matches[-1]
        date = _parse_date(movement_date_match.group(0), fallback_year=fallback_year)
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        movement_segment = normalize_text(line.text[date_matches[-2].end() : movement_date_match.start()])
        amount = abs(parse_pdf_amount(amount_token.value))
        if re.search(r"(?:^|\s)S(?:\s|$)", movement_segment):
            amount = -amount
        description = _clean_description(line.text[: movement_date_match.start()], amount_tokens=())
        rows.append(_build_row(date=date, description=description, amount=amount, amount_token=amount_token, source=line))
    return rows


def _parse_payment_rows(lines: list[_PdfLine], *, fallback_year: int | None) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        if "EFETUADO" not in normalize_text(line.text):
            continue
        date_matches = list(_DATE_PATTERN.finditer(line.text))
        amount_tokens = find_amount_tokens(line.text)
        if not date_matches or not amount_tokens:
            continue
        date = _parse_date(date_matches[-1].group(0), fallback_year=fallback_year)
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(line.text[: date_matches[-1].start()], amount_tokens=())
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=-abs(parse_pdf_amount(amount_token.value)),
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_received_transfer_rows(
    lines: list[_PdfLine], *, fallback_year: int | None
) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        if "EMITIDA EM" in normalized or "RECEBIDO EM" in normalized:
            continue
        date_matches = list(_DATE_PATTERN.finditer(line.text))
        amount_tokens = find_amount_tokens(line.text)
        if not date_matches or not amount_tokens:
            continue
        date = _parse_date(date_matches[-1].group(0), fallback_year=fallback_year)
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(line.text[: date_matches[-1].start()], amount_tokens=())
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=abs(parse_pdf_amount(amount_token.value)),
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_transfer_receipt(lines: list[_PdfLine], *, fallback_year: int | None) -> list[_ParsedTransaction]:
    date_line = next((line for line in lines if "DATA DA TRANSFERENCIA" in normalize_text(line.text)), None)
    amount_line = next((line for line in lines if normalize_text(line.text).startswith("VALOR")), None)
    type_line = next((line for line in lines if "TIPO DE PAGAMENTO" in normalize_text(line.text)), None)
    if date_line is None or amount_line is None:
        return []
    date_match = _DATE_PATTERN.search(date_line.text)
    amount_tokens = find_amount_tokens(amount_line.text)
    if date_match is None or not amount_tokens:
        return []
    date = _parse_date(date_match.group(0), fallback_year=fallback_year)
    if date is None:
        return []
    amount_token = amount_tokens[-1]
    description = "PIX TRANSFERENCIA"
    if type_line is not None and ":" in type_line.text:
        description = type_line.text.split(":", 1)[1].strip() or description
    return [
        _build_row(
            date=date,
            description=description,
            amount=-abs(parse_pdf_amount(amount_token.value)),
            amount_token=amount_token,
            source=date_line,
            forced_sign=True,
        )
    ]


def _build_row(
    *,
    date: str,
    description: str,
    amount: float,
    amount_token: AmountToken,
    source: _PdfLine,
    forced_sign: bool = False,
) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=" ".join(description.split()),
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        has_explicit_amount_sign=forced_sign or has_explicit_amount_sign(amount_token.value),
    )


def _parse_date(raw_date: str, *, fallback_year: int | None) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=fallback_year)
    except InvalidFileContentError:
        return None


def _find_savings_amount_tokens(text: str) -> list[AmountToken]:
    return [
        AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))
        for match in _SAVINGS_AMOUNT_PATTERN.finditer(text)
    ]


def _infer_itau_year(lines: list[_PdfLine]) -> int | None:
    inferred_year = infer_default_statement_year_from_lines(lines)
    if inferred_year is not None:
        return inferred_year
    month_header_years = [
        int(year)
        for line in lines
        for year in _MONTH_YEAR_PATTERN.findall(normalize_text(line.text))
    ]
    if month_header_years:
        return Counter(month_header_years).most_common(1)[0][0]
    years = [int(year) for line in lines for year in _YEAR_PATTERN.findall(line.text)]
    if not years:
        return None
    return Counter(years).most_common(1)[0][0]


def _is_balance_or_summary_row(raw_description: str) -> bool:
    normalized = normalize_text(raw_description)
    if normalized.startswith("SDO ") or normalized == "SDO":
        return True
    return any(token in normalized for token in _BALANCE_DESCRIPTIONS)


def _clean_description(raw_text: str, *, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = raw_text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:EFETUADO|AGENDADO|CANCELADO)\s*$", "", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())
