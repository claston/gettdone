from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.application.normalization.date import MONTH_TO_NUMBER
from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BANRISUL_MONOSPACE_LAYOUT = "banrisul_extrato_texto_movimentos_conta_corrente_v1"

_MONTH_SECTION_PATTERN = re.compile(
    r"\bMOVIMENTOS\s+(?P<month>JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)"
    r"\s*/\s*(?P<year>\d{4})\b"
)
_DAY_PATTERN = re.compile(r"^(?P<day>0[1-9]|[12]\d|3[01])$")
_DOCUMENT_PATTERN = re.compile(r"^\d{4,20}$")
_AMOUNT_TOKEN = r"\.?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}[+-]?"
_AMOUNT_ONLY_PATTERN = re.compile(rf"^(?P<amount>{_AMOUNT_TOKEN})$")
_FIXED_WIDTH_ROW_PATTERN = re.compile(
    rf"^\s*(?:(?P<day>0[1-9]|[12]\d|3[01])\s+)?"
    rf"(?P<description>.+?)\s+(?P<document>\d{{4,20}})\s+"
    rf"(?P<amount>{_AMOUNT_TOKEN})\s*$"
)
_IGNORED_EXACT_LINES = {
    "DIA HISTORICO",
    "DOCUMENTO",
    "VALOR",
    "DIA HISTORICO DOCUMENTO VALOR",
}


@dataclass(frozen=True, slots=True)
class BanrisulLayoutParser:
    layout_names: frozenset[str] = frozenset({BANRISUL_MONOSPACE_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name not in self.layout_names:
            return None
        rows = _parse_monospace_rows(lines, context=context)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_banrisul_monospace",
            selection_reason="layout_specific_banrisul_monospace",
        )


def _parse_monospace_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    in_movement_table = False
    reference_month_year = context.reference_month_year
    current_day: int | None = None
    pending_description_parts: list[str] = []
    pending_document: str | None = None
    pending_source: _PdfLine | None = None
    parsed_rows: list[_ParsedTransaction] = []

    for line in lines:
        normalized = normalize_text(line.text)
        if "MOVIMENTOS DA CONTA CORRENTE" in normalized:
            in_movement_table = True
            continue
        if not in_movement_table:
            continue

        month_match = _MONTH_SECTION_PATTERN.search(normalized)
        if month_match is not None:
            reference_month_year = (
                MONTH_TO_NUMBER[month_match.group("month")],
                int(month_match.group("year")),
            )
            current_day = None
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        if _should_ignore_line(normalized):
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        fixed_width_match = _FIXED_WIDTH_ROW_PATTERN.fullmatch(line.text)
        if fixed_width_match is not None and reference_month_year is not None:
            raw_day = fixed_width_match.group("day")
            if raw_day is not None:
                current_day = int(raw_day)
            if current_day is None:
                continue
            parsed_rows.append(
                _build_row(
                    day=current_day,
                    reference_month_year=reference_month_year,
                    description=fixed_width_match.group("description"),
                    document=fixed_width_match.group("document"),
                    raw_amount=fixed_width_match.group("amount"),
                    source=line,
                )
            )
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        day_match = _DAY_PATTERN.fullmatch(line.text.strip())
        if day_match is not None:
            current_day = int(day_match.group("day"))
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        amount_match = _AMOUNT_ONLY_PATTERN.fullmatch(line.text.strip())
        if amount_match is not None:
            if (
                current_day is not None
                and reference_month_year is not None
                and pending_description_parts
                and pending_source is not None
            ):
                parsed_rows.append(
                    _build_row(
                        day=current_day,
                        reference_month_year=reference_month_year,
                        description=" ".join(pending_description_parts),
                        document=pending_document,
                        raw_amount=amount_match.group("amount"),
                        source=pending_source,
                    )
                )
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        stripped = line.text.strip()
        if _DOCUMENT_PATTERN.fullmatch(stripped) and pending_description_parts and pending_document is None:
            pending_document = stripped
            continue

        if current_day is None or reference_month_year is None:
            continue
        if pending_source is None:
            pending_source = line
        pending_description_parts.append(stripped)

    return parsed_rows


def _should_ignore_line(normalized: str) -> bool:
    if normalized in _IGNORED_EXACT_LINES:
        return True
    if normalized.startswith(("SALDO ANT", "SALDO ANTERIOR", "SALDO FINAL")):
        return True
    compact = normalized.replace(" ", "")
    return bool(compact) and set(compact) <= {"+", "-"}


def _build_row(
    *,
    day: int,
    reference_month_year: tuple[int, int],
    description: str,
    document: str | None,
    raw_amount: str,
    source: _PdfLine,
) -> _ParsedTransaction:
    month, year = reference_month_year
    date = datetime(year, month, day).strftime("%Y-%m-%d")
    normalized_amount = _normalize_leading_dot_amount(raw_amount)
    amount = parse_pdf_amount(normalized_amount)
    return build_parsed_transaction(
        date=date,
        description=" ".join(description.split()),
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        external_reference_id=document,
        has_explicit_amount_sign=has_explicit_amount_sign(normalized_amount),
    )


def _normalize_leading_dot_amount(raw_amount: str) -> str:
    value = raw_amount.strip()
    if re.fullmatch(r"\.\d{1,3},\d{2}[+-]?", value):
        return value[1:]
    return value
