from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.application.normalization.date import MONTH_TO_NUMBER
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BANESTES_INTERNET_BANKING_LAYOUT = "banestes_internet_banking_extrato_v1"

_DAY_PATTERN = re.compile(r"^(?P<day>0?[1-9]|[12]\d|3[01])$")
_MONTH_PATTERN = re.compile(r"^(?P<month>JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)$")
_PERIOD_PATTERN = re.compile(
    r"\bPERIODO\s*:\s*(?P<start_day>\d{1,2})/(?P<start_month>\d{1,2})/(?P<start_year>\d{4})"
    r"\s+A\s+(?P<end_day>\d{1,2})/(?P<end_month>\d{1,2})/(?P<end_year>\d{4})\b"
)
_AMOUNT_TOKEN = r"[+\-]?\s*(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}"
_AMOUNT_ONLY_PATTERN = re.compile(rf"^(?P<amount>{_AMOUNT_TOKEN})$")
_TRAILING_AMOUNT_PATTERN = re.compile(rf"(?P<amount>{_AMOUNT_TOKEN})\s*$")
_CREDIT_MARKER = "\ue5d8"
_DEBIT_MARKER = "\ue5db"
_DECORATIVE_MARKERS = frozenset({"\ue8b0", "\ue897"})
_IGNORED_PREFIXES = (
    "SALDO ANTERIOR",
    "SALDO CONTA/RENDE+",
    "SALDO TOTAL",
    "CHEQUE ESPECIAL DISPONIVEL",
    "DATA LANCAMENTO VALOR",
    "BANESTES INTERNET BANKING",
    "HTTPS://",
)


@dataclass(frozen=True, slots=True)
class _StatementPeriod:
    start_month: int
    start_year: int
    end_month: int
    end_year: int


@dataclass(slots=True)
class _PendingTransaction:
    source: _PdfLine
    direction: int
    description_parts: list[str]


@dataclass(frozen=True, slots=True)
class BanestesLayoutParser:
    layout_names: frozenset[str] = frozenset({BANESTES_INTERNET_BANKING_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != BANESTES_INTERNET_BANKING_LAYOUT:
            return None
        rows = _parse_internet_banking_rows(lines, context=context)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_banestes",
            selection_reason=f"layout_specific_banestes:{layout_name}",
        )


def _parse_internet_banking_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    period = _find_statement_period(lines)
    current_day: int | None = None
    current_month: int | None = None
    pending_day: int | None = None
    pending: _PendingTransaction | None = None
    rows: list[_ParsedTransaction] = []

    for line in lines:
        stripped = line.text.strip()
        normalized = normalize_text(stripped)

        if normalized.startswith("LANCAMENTOS PREVISTOS"):
            break

        day_match = _DAY_PATTERN.fullmatch(stripped)
        if day_match is not None:
            pending_day = int(day_match.group("day"))
            pending = None
            continue

        month_match = _MONTH_PATTERN.fullmatch(normalized)
        if month_match is not None and pending_day is not None:
            current_day = pending_day
            current_month = MONTH_TO_NUMBER[month_match.group("month")]
            pending_day = None
            pending = None
            continue

        if current_day is None or current_month is None:
            continue
        if not stripped or stripped in _DECORATIVE_MARKERS:
            continue
        if normalized.startswith(_IGNORED_PREFIXES):
            pending = None
            continue

        direction = _transaction_direction(stripped)
        if direction is not None:
            pending = _PendingTransaction(
                source=line,
                direction=direction,
                description_parts=[],
            )
            transaction_text = stripped[1:].strip()
            amount_match = _TRAILING_AMOUNT_PATTERN.search(transaction_text)
            if amount_match is None:
                if transaction_text:
                    pending.description_parts.append(transaction_text)
                continue
            description = transaction_text[: amount_match.start()].strip()
            if description:
                pending.description_parts.append(description)
            row = _build_pending_row(
                pending=pending,
                raw_amount=amount_match.group("amount"),
                day=current_day,
                month=current_month,
                period=period,
                context=context,
            )
            if row is not None:
                rows.append(row)
            pending = None
            continue

        if pending is None:
            continue
        amount_match = _AMOUNT_ONLY_PATTERN.fullmatch(stripped)
        if amount_match is not None:
            row = _build_pending_row(
                pending=pending,
                raw_amount=amount_match.group("amount"),
                day=current_day,
                month=current_month,
                period=period,
                context=context,
            )
            if row is not None:
                rows.append(row)
            pending = None
            continue
        pending.description_parts.append(stripped)

    return rows


def _find_statement_period(lines: list[_PdfLine]) -> _StatementPeriod | None:
    for line in lines:
        match = _PERIOD_PATTERN.search(normalize_text(line.text))
        if match is None:
            continue
        return _StatementPeriod(
            start_month=int(match.group("start_month")),
            start_year=int(match.group("start_year")),
            end_month=int(match.group("end_month")),
            end_year=int(match.group("end_year")),
        )
    return None


def _transaction_direction(value: str) -> int | None:
    if value.startswith(_CREDIT_MARKER):
        return 1
    if value.startswith(_DEBIT_MARKER):
        return -1
    return None


def _build_pending_row(
    *,
    pending: _PendingTransaction,
    raw_amount: str,
    day: int,
    month: int,
    period: _StatementPeriod | None,
    context: LayoutSpecificParseContext,
) -> _ParsedTransaction | None:
    description = " ".join(" ".join(pending.description_parts).split())
    if not description:
        return None
    year = _resolve_transaction_year(month=month, period=period, context=context)
    if year is None:
        return None
    try:
        transaction_date = date(year, month, day).isoformat()
    except ValueError:
        return None
    unsigned_amount = abs(parse_pdf_amount(raw_amount))
    amount = unsigned_amount if pending.direction > 0 else -unsigned_amount
    return build_parsed_transaction(
        date=transaction_date,
        description=description,
        amount=amount,
        source_page=pending.source.page_number,
        source_line=pending.source.line_number,
        has_explicit_amount_sign=True,
    )


def _resolve_transaction_year(
    *,
    month: int,
    period: _StatementPeriod | None,
    context: LayoutSpecificParseContext,
) -> int | None:
    if period is not None:
        if period.start_year == period.end_year:
            return period.start_year
        if month >= period.start_month:
            return period.start_year
        if month <= period.end_month:
            return period.end_year
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return None
