from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
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

BB_CDB_LAYOUT = "banco_do_brasil_cdb_rdb_bb_reaplic_v1"

_MOVEMENT_PATTERN = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2})\s+"
    r"(?P<history>APLICACAO|RESGATE|RENDIMENTO MENSAL)"
    r"(?:\s*-?\s*(?P<reference>\d{6,20}|\[IDENTIFICADOR\]))?\s*$"
)
_IGNORED_BALANCE_PATTERN = re.compile(
    r"^\d{1,2}/\d{1,2}\s+(?:SALDO ANTERIOR|SALDO FINAL)\b"
)
_SECONDARY_SECTION_MARKERS = (
    "SALDO NOS ULTIMOS 6 MESES",
    "DATA CAPITAL EM SER JUROS",
    "RESUMO DOS DEPOSITOS EM SER",
    "NUMERO DT.APLIC CAPITAL INICIAL",
    "RENDIMENTO BRUTO NO PERIODO POR DEPOSITO",
)
_DETAIL_FIELDS = (
    ("VALOR LIQUIDO", "net"),
    ("VALOR CAPITAL", "capital"),
    ("VALOR JUROS ATE MES ANT", "prior_interest"),
    ("VALOR JUROS NO MES", "monthly_interest"),
    ("VALOR JUROS", "interest"),
    ("VALOR IOF", "iof"),
    ("VALOR IR", "income_tax"),
)


@dataclass(slots=True)
class _PendingMovement:
    raw_date: str
    history: str
    reference: str | None
    source_page: int
    source_line: int
    values: dict[str, float]


@dataclass(frozen=True, slots=True)
class BancoDoBrasilCdbLayoutParser:
    layout_names: frozenset[str] = frozenset({BB_CDB_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != BB_CDB_LAYOUT:
            return None
        rows = _parse_cdb_rows(lines, context=context)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_banco_do_brasil_cdb",
            selection_reason=f"layout_specific_banco_do_brasil_cdb:{layout_name}",
        )


def _parse_cdb_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    fallback_year = infer_default_statement_year_from_lines(lines)
    if fallback_year is None and context.reference_month_year is not None:
        fallback_year = context.reference_month_year[1]

    rows: list[_ParsedTransaction] = []
    pending: _PendingMovement | None = None
    for line in lines:
        normalized = normalize_text(line.text)
        if any(marker in normalized for marker in _SECONDARY_SECTION_MARKERS):
            _append_pending(rows, pending, fallback_year=fallback_year)
            break

        movement_match = _MOVEMENT_PATTERN.match(normalized)
        if movement_match is not None:
            _append_pending(rows, pending, fallback_year=fallback_year)
            raw_reference = movement_match.group("reference")
            pending = _PendingMovement(
                raw_date=movement_match.group("date"),
                history=movement_match.group("history"),
                reference=raw_reference if raw_reference and raw_reference.isdigit() else None,
                source_page=line.page_number,
                source_line=line.line_number,
                values={},
            )
            continue

        if _IGNORED_BALANCE_PATTERN.match(normalized):
            _append_pending(rows, pending, fallback_year=fallback_year)
            pending = None
            continue

        if pending is None:
            continue
        detail = _parse_detail_value(line.text, normalized=normalized)
        if detail is not None:
            field, value = detail
            pending.values[field] = value

    else:
        _append_pending(rows, pending, fallback_year=fallback_year)

    return rows


def _parse_detail_value(raw_text: str, *, normalized: str) -> tuple[str, float] | None:
    for prefix, field in _DETAIL_FIELDS:
        if normalized.startswith(prefix):
            amount_tokens = find_amount_tokens(raw_text)
            if amount_tokens:
                return field, parse_pdf_amount(amount_tokens[-1].value)
            return None
    return None


def _append_pending(
    rows: list[_ParsedTransaction],
    pending: _PendingMovement | None,
    *,
    fallback_year: int | None,
) -> None:
    if pending is None:
        return
    amount = _movement_amount(pending)
    if amount is None:
        return
    try:
        date = parse_row_date(pending.raw_date, fallback_year=fallback_year)
    except InvalidFileContentError:
        return
    description = {
        "APLICACAO": "APLICACAO CDB",
        "RESGATE": "RESGATE CDB",
        "RENDIMENTO MENSAL": "RENDIMENTO MENSAL CDB",
    }[pending.history]
    rows.append(
        build_parsed_transaction(
            date=date,
            description=description,
            amount=amount,
            source_page=pending.source_page,
            source_line=pending.source_line,
            external_reference_id=pending.reference,
            has_explicit_amount_sign=True,
        )
    )


def _movement_amount(pending: _PendingMovement) -> float | None:
    if pending.history == "APLICACAO":
        capital = pending.values.get("capital")
        return -abs(capital) if capital is not None else None
    if pending.history == "RESGATE":
        net = pending.values.get("net")
        return abs(net) if net is not None else None
    interest = pending.values.get("interest")
    return abs(interest) if interest is not None else None
