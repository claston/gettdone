from __future__ import annotations

from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import is_amount_only_row, is_date_only_row
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

ASAAS_MOVEMENTS_LAYOUT = "asaas_extrato_conta_digital_movimentacoes_v1"

_HEADER_NOISE = {
    "DATA",
    "MOVIMENTACOES",
    "VALOR",
}


@dataclass(frozen=True, slots=True)
class AsaasLayoutParser:
    layout_names: frozenset[str] = frozenset({ASAAS_MOVEMENTS_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name not in self.layout_names:
            return None
        rows = _parse_movement_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_asaas",
            selection_reason="layout_specific_asaas:date_description_amount",
        )


def _parse_movement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    inside_table = False
    saw_movements_header = False
    pending_description: list[str] = []
    current_date: str | None = None
    current_source: _PdfLine | None = None
    description_parts: list[str] = []
    pending_amount: tuple[float, bool] | None = None

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized == "MOVIMENTACOES":
            saw_movements_header = True
            continue
        if saw_movements_header and normalized == "VALOR":
            inside_table = True
            pending_description.clear()
            continue
        if not inside_table:
            continue
        if normalized.startswith("ASAAS GESTAO FINANCEIRA"):
            break
        if normalized in _HEADER_NOISE or not normalized:
            continue

        if is_date_only_row(line.text):
            _append_pending_row(
                rows,
                date=current_date,
                source=current_source,
                description_parts=description_parts,
                pending_amount=pending_amount,
            )
            parsed_date = _safe_parse_date(line.text)
            if parsed_date is None:
                continue
            current_date = parsed_date
            current_source = line
            description_parts = pending_description
            pending_description = []
            pending_amount = None
            continue

        if is_amount_only_row(line.text):
            if current_date is None or current_source is None:
                continue
            amount = parse_pdf_amount(line.text)
            if not description_parts:
                pending_amount = (amount, has_explicit_amount_sign(line.text))
                continue
            _append_pending_row(
                rows,
                date=current_date,
                source=current_source,
                description_parts=description_parts,
                pending_amount=(amount, has_explicit_amount_sign(line.text)),
            )
            current_date = None
            current_source = None
            description_parts = []
            pending_amount = None
            continue

        if current_date is None:
            pending_description.append(line.text.strip())
        else:
            description_parts.append(line.text.strip())

    _append_pending_row(
        rows,
        date=current_date,
        source=current_source,
        description_parts=description_parts,
        pending_amount=pending_amount,
    )
    return rows


def _append_pending_row(
    rows: list[_ParsedTransaction],
    *,
    date: str | None,
    source: _PdfLine | None,
    description_parts: list[str],
    pending_amount: tuple[float, bool] | None,
) -> None:
    if date is None or source is None or not description_parts or pending_amount is None:
        return
    amount, explicit_sign = pending_amount
    rows.append(
        build_parsed_transaction(
            date=date,
            description=" ".join(description_parts),
            amount=amount,
            source_page=source.page_number,
            source_line=source.line_number,
            has_explicit_amount_sign=explicit_sign,
        )
    )


def _safe_parse_date(raw_date: str) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None
