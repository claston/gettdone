from __future__ import annotations

from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import is_amount_only_row, is_date_only_row
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BLING_CASH_REPORT_LAYOUT = "bling_relatorio_caixa_bancos_v1"

_HEADER_NOISE = {
    "DATA",
    "CLIENTE/FORNECEDOR",
    "HISTORICO",
    "CATEGORIA",
    "ENTRADA",
    "SAIDA",
}
_POSITIVE_HINTS = (
    "VENDA",
    "RECEBIMENTO",
    "CREDITO",
    "DEPOSITO",
)
_NEGATIVE_HINTS = (
    "PAGAMENTO",
    "TRANSFERENCIA PIX",
    "TARIFA",
    "REF A NF",
    "REF. A NF",
    "COMPRA",
)


@dataclass(frozen=True, slots=True)
class BlingLayoutParser:
    layout_names: frozenset[str] = frozenset({BLING_CASH_REPORT_LAYOUT})

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
        rows = _parse_cash_report_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_bling",
            selection_reason="layout_specific_bling:date_description_amount",
        )


def _parse_cash_report_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    inside_table = False
    pending_description: list[str] = []
    current_date: str | None = None
    current_source: _PdfLine | None = None
    description_parts: list[str] = []

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized == "SAIDA":
            inside_table = True
            pending_description.clear()
            continue
        if not inside_table:
            continue
        if normalized == "TOTAIS":
            break
        if (
            not normalized
            or normalized in _HEADER_NOISE
            or normalized.startswith("BLING - RELATORIO DE CAIXA E BANCOS")
            or normalized.startswith("SALDO DO DIA")
        ):
            continue

        if is_date_only_row(line.text):
            parsed_date = _safe_parse_date(line.text)
            if parsed_date is None:
                continue
            current_date = parsed_date
            current_source = line
            description_parts = pending_description
            pending_description = []
            continue

        if is_amount_only_row(line.text):
            if current_date is None or current_source is None or not description_parts:
                continue
            unsigned_amount = abs(parse_pdf_amount(line.text))
            description = " ".join(description_parts)
            amount = _resolve_amount_sign(unsigned_amount, description=description)
            rows.append(
                build_parsed_transaction(
                    date=current_date,
                    description=description,
                    amount=amount,
                    source_page=current_source.page_number,
                    source_line=current_source.line_number,
                    has_explicit_amount_sign=False,
                )
            )
            current_date = None
            current_source = None
            description_parts = []
            continue

        if current_date is None:
            pending_description.append(line.text.strip())
        else:
            description_parts.append(line.text.strip())

    return rows


def _resolve_amount_sign(amount: float, *, description: str) -> float:
    normalized = normalize_text(description)
    if any(hint in normalized for hint in _POSITIVE_HINTS):
        return amount
    if ("REF A NF" in normalized or "REF. A NF" in normalized) and amount > 10:
        return amount
    if any(hint in normalized for hint in _NEGATIVE_HINTS):
        return -amount
    return amount


def _safe_parse_date(raw_date: str) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None
