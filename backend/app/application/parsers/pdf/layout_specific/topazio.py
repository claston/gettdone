from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    find_amount_tokens,
    has_amount_token_explicit_sign,
    parse_amount_token,
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

TOPAZIO_CURRENT_ACCOUNT_LAYOUT = "banco_topazio_extrato_conta_corrente_lista_v1"

_DATE_ROW_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2})\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_TRAILING_CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?P<prefix>[+\-\u2212]?\s*R\$\s*)(?P<number>\d[\d.\s]*,\s*\d\s*\d)\s*$",
    flags=re.IGNORECASE,
)
_BALANCE_TOKENS = (
    "SALDO DISPONIVEL",
    "SALDO FINAL",
    "SALDO DO DIA",
    "SALDO INICIAL",
    "SALDO ANTERIOR",
)


@dataclass(frozen=True, slots=True)
class TopazioLayoutParser:
    layout_names: frozenset[str] = frozenset({TOPAZIO_CURRENT_ACCOUNT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != TOPAZIO_CURRENT_ACCOUNT_LAYOUT:
            return None

        fallback_year = _resolve_fallback_year(lines, context=context)
        rows = _parse_transaction_rows(lines, fallback_year=fallback_year)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_topazio",
            selection_reason=f"layout_specific_topazio:{layout_name}",
        )


def _parse_transaction_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_ROW_PATTERN.match(line.text)
        if match is None:
            continue

        rest = _compact_spaced_currency_amount(match.group("rest"))
        amount_tokens = tuple(find_amount_tokens(rest))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(rest, amount_token=amount_token)
        if not description or _is_balance_description(description):
            continue

        rows.append(
            build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=parse_amount_token(amount_token),
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=has_amount_token_explicit_sign(amount_token),
            )
        )
    return rows


def _compact_spaced_currency_amount(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        compact_number = re.sub(r"\s+", "", match.group("number"))
        return f"{match.group('prefix')}{compact_number}"

    return _TRAILING_CURRENCY_AMOUNT_PATTERN.sub(replace, value)


def _clean_description(value: str, *, amount_token: AmountToken) -> str:
    return " ".join(value[: amount_token.start].strip(" -|:").split())


def _is_balance_description(value: str) -> bool:
    normalized = normalize_text(value)
    return any(token in normalized for token in _BALANCE_TOKENS)


def _resolve_fallback_year(lines: list[_PdfLine], *, context: LayoutSpecificParseContext) -> int:
    inferred = infer_default_statement_year_from_lines(lines)
    if inferred is not None:
        return inferred
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return datetime.now(timezone.utc).year
