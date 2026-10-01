from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BTG_PJ_CURRENT_ACCOUNT_LAYOUT = "btg_pactual_conta_corrente_pj_v1"

_DATE_ROW_PATTERN = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<rest>.+)$")
_NON_TRANSACTION_LABELS = (
    "SALDO DE ABERTURA",
    "SALDO DE FECHAMENTO",
    "SALDO BLOQUEADO",
    "TOTAL DE ENTRADAS",
    "TOTAL DE SAIDAS",
)


@dataclass(frozen=True, slots=True)
class BtgPactualLayoutParser:
    layout_names: frozenset[str] = frozenset({BTG_PJ_CURRENT_ACCOUNT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        del context
        if layout_name != BTG_PJ_CURRENT_ACCOUNT_LAYOUT:
            return None

        rows = _parse_current_account_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_btg_pactual",
            selection_reason=f"layout_specific_btg_pactual:{layout_name}",
        )


def _parse_current_account_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_ROW_PATTERN.match(line.text)
        if match is None:
            continue

        rest = match.group("rest")
        amount_tokens = tuple(find_amount_tokens(rest))
        if len(amount_tokens) < 2:
            continue

        amount_token = amount_tokens[-2]
        balance_token = amount_tokens[-1]
        description = _clean_description(
            rest,
            amount_start=amount_token.start,
            balance_end=balance_token.end,
        )
        if not description or _is_non_transaction(description):
            continue

        rows.append(
            build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=None),
                description=description,
                amount=parse_pdf_amount(amount_token.value),
                running_balance=parse_pdf_amount(balance_token.value),
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _clean_description(raw_text: str, *, amount_start: int, balance_end: int) -> str:
    value = raw_text[:amount_start] + " " + raw_text[balance_end:]
    return " ".join(value.strip(" -|:").split())


def _is_non_transaction(description: str) -> bool:
    normalized = normalize_text(description)
    return any(label in normalized for label in _NON_TRANSACTION_LABELS)
