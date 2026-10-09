from __future__ import annotations

from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BB_RENDE_FACIL_LAYOUT = "banco_do_brasil_rende_facil_historico_movimentacao_v1"


@dataclass(frozen=True, slots=True)
class BancoDoBrasilRendeFacilLayoutParser:
    layout_names: frozenset[str] = frozenset({BB_RENDE_FACIL_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != BB_RENDE_FACIL_LAYOUT:
            return None
        rows = _parse_rende_facil_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_banco_do_brasil_rende_facil",
            selection_reason=f"layout_specific_banco_do_brasil_rende_facil:{layout_name}",
        )


def _parse_rende_facil_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        row = _parse_rende_facil_row(line)
        if row is not None:
            rows.append(row)
    return rows


def _parse_rende_facil_row(line: _PdfLine) -> _ParsedTransaction | None:
    stripped = line.text.strip()
    if len(stripped) < 10:
        return None
    raw_date = stripped[:10]
    try:
        date = parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None

    amount_tokens = find_amount_tokens(stripped)
    if len(amount_tokens) < 5:
        return None
    history = normalize_text(stripped[10 : amount_tokens[0].start]).strip()
    if "APLIC" in history:
        description = "APLICACAO BB RENDE FACIL"
        amount = -abs(parse_pdf_amount(amount_tokens[-1].value))
    elif "RESGATE" in history:
        description = "RESGATE BB RENDE FACIL"
        amount = abs(parse_pdf_amount(amount_tokens[-1].value))
    else:
        return None

    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=line.page_number,
        source_line=line.line_number,
        has_explicit_amount_sign=True,
    )
