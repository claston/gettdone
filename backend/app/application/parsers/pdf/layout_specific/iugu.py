from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

IUGU_BANK_MOVEMENT_LAYOUT = "iugu_movimentacao_bancaria_v1"

_ROW_ID_PATTERN = re.compile(r"^#(?P<transaction_id>\d+)$")
_DATE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_AMOUNT_TOKEN = r"[+-]?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}[+-]?"
_AMOUNT_AND_BALANCE_PATTERN = re.compile(
    rf"\b\d+/\d+\s+R\$\s*(?P<amount>{_AMOUNT_TOKEN})\s*R\$\s*(?P<balance>{_AMOUNT_TOKEN})\b",
    flags=re.IGNORECASE,
)
_INSTALLMENT_AMOUNT_PREFIX_PATTERN = re.compile(r"\b\d+/\d+\s+R\$", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IuguLayoutParser:
    layout_names: frozenset[str] = frozenset({IUGU_BANK_MOVEMENT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name != IUGU_BANK_MOVEMENT_LAYOUT:
            return None

        rows = _parse_movement_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_iugu_movimentacao_bancaria",
            selection_reason="layout_specific_iugu_movimentacao_bancaria:transaction_id_blocks",
        )


def _parse_movement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    row_starts = [index for index, line in enumerate(lines) if _ROW_ID_PATTERN.fullmatch(line.text.strip())]
    rows: list[_ParsedTransaction] = []
    for row_number, start_index in enumerate(row_starts):
        end_index = row_starts[row_number + 1] if row_number + 1 < len(row_starts) else len(lines)
        row = _parse_movement_block(lines[start_index:end_index])
        if row is not None:
            rows.append(row)
    return rows


def _parse_movement_block(lines: list[_PdfLine]) -> _ParsedTransaction | None:
    if not lines:
        return None
    transaction_match = _ROW_ID_PATTERN.fullmatch(lines[0].text.strip())
    if transaction_match is None:
        return None

    date_line = next((line for line in lines[1:] if _DATE_PATTERN.fullmatch(line.text.strip())), None)
    if date_line is None:
        return None

    joined = " ".join(line.text.strip() for line in lines if line.text.strip())
    amount_match = _AMOUNT_AND_BALANCE_PATTERN.search(joined)
    if amount_match is None:
        return None

    description = _extract_description(lines)
    if not description:
        return None

    raw_amount = amount_match.group("amount")
    return build_parsed_transaction(
        date=parse_row_date(date_line.text.strip(), fallback_year=None),
        description=description,
        amount=parse_pdf_amount(raw_amount),
        source_page=lines[0].page_number,
        source_line=lines[0].line_number,
        running_balance=parse_pdf_amount(amount_match.group("balance")),
        external_reference_id=transaction_match.group("transaction_id"),
        has_explicit_amount_sign=has_explicit_amount_sign(raw_amount),
    )


def _extract_description(lines: list[_PdfLine]) -> str:
    status_index = next(
        (
            index
            for index, line in enumerate(lines)
            if normalize_text(line.text).startswith("AUTORIZADO")
        ),
        None,
    )
    if status_index is None:
        return ""

    description_parts: list[str] = []
    for line in lines[status_index + 1 :]:
        if _INSTALLMENT_AMOUNT_PREFIX_PATTERN.search(line.text):
            break
        value = line.text.strip()
        if value:
            description_parts.append(value)
    return " ".join(" ".join(description_parts).split())
