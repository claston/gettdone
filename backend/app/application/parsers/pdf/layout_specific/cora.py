from __future__ import annotations

import re
from dataclasses import dataclass, replace

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
    normalize_text,
)
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

CORA_AVAILABLE_BALANCE_LAYOUT = "cora_extrato_periodo_disponivel_v1"

_DATE_GROUP_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\b.*\bSALDO\s+DO\s+DIA\b",
    flags=re.IGNORECASE,
)
_TRANSACTION_SECTION_TOKEN = "TRANSACOES"
_IGNORED_LINE_PREFIXES = (
    "CNPJ",
    "AGENCIA",
    "CONTA",
    "CORA SCFI",
    "OUVIDORIA",
    "EXTRATO GERADO NO DIA",
    "SALDO INICIAL DISPONIVEL",
    "SALDO FINAL DISPONIVEL",
    "TOTAL DE ENTRADAS",
    "TOTAL DE SAIDAS",
)


@dataclass(frozen=True, slots=True)
class CoraLayoutParser:
    layout_names: frozenset[str] = frozenset({CORA_AVAILABLE_BALANCE_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name != CORA_AVAILABLE_BALANCE_LAYOUT:
            return None

        rows = _parse_available_balance_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_cora",
            selection_reason=f"layout_specific_cora:{layout_name}",
        )


@dataclass(frozen=True, slots=True)
class _CoraTransactionGroup:
    date: str
    start: int
    end: int
    daily_balance: float | None


def _parse_available_balance_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    groups: list[_CoraTransactionGroup] = []
    inside_transactions = False
    current_date: str | None = None
    current_daily_balance: float | None = None
    current_group_start = 0

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized == _TRANSACTION_SECTION_TOKEN:
            inside_transactions = True
            continue
        if not inside_transactions:
            continue

        date_match = _DATE_GROUP_PATTERN.match(normalized)
        if date_match is not None:
            _close_group(
                groups,
                rows,
                date=current_date,
                group_start=current_group_start,
                daily_balance=current_daily_balance,
            )
            current_group_start = len(rows)
            current_date = parse_row_date(date_match.group("date"), fallback_year=None)
            current_daily_balance = _last_amount(line.text)
            continue

        if current_date is None or normalized.startswith(_IGNORED_LINE_PREFIXES):
            continue

        amount_token = _last_amount_token(line.text)
        if amount_token is None or not has_explicit_amount_sign(amount_token.value):
            continue
        description = _remove_amount_token(line.text, amount_token=amount_token)
        if not description:
            continue
        rows.append(
            build_parsed_transaction(
                date=current_date,
                description=description,
                amount=parse_pdf_amount(amount_token.value),
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )

    _close_group(
        groups,
        rows,
        date=current_date,
        group_start=current_group_start,
        daily_balance=current_daily_balance,
    )
    _attach_daily_balances(rows, groups=groups)
    return rows


def _close_group(
    groups: list[_CoraTransactionGroup],
    rows: list[_ParsedTransaction],
    *,
    date: str | None,
    group_start: int,
    daily_balance: float | None,
) -> None:
    if date is None or len(rows) <= group_start:
        return
    groups.append(
        _CoraTransactionGroup(
            date=date,
            start=group_start,
            end=len(rows),
            daily_balance=daily_balance,
        )
    )


def _attach_daily_balances(
    rows: list[_ParsedTransaction],
    *,
    groups: list[_CoraTransactionGroup],
) -> None:
    descending = len(groups) >= 2 and groups[0].date > groups[-1].date
    for group in groups:
        if group.daily_balance is None:
            continue
        balance_row_index = group.start if descending else group.end - 1
        rows[balance_row_index] = replace(
            rows[balance_row_index],
            running_balance=group.daily_balance,
        )


def _last_amount(value: str) -> float | None:
    token = _last_amount_token(value)
    return parse_pdf_amount(token.value) if token is not None else None


def _last_amount_token(value: str) -> AmountToken | None:
    tokens = tuple(find_amount_tokens(value))
    if not tokens:
        return None
    token = tokens[-1]
    if value[token.end :].strip():
        return None
    return token


def _remove_amount_token(value: str, *, amount_token: AmountToken) -> str:
    without_amount = value[: amount_token.start] + " " + value[amount_token.end :]
    return " ".join(without_amount.strip(" -|:").split())
