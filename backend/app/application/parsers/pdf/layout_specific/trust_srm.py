from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
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

TRUST_SRM_STATEMENT_LAYOUT = "trust_srm_bank_extrato_conta_corrente_v1"

_DATE_LINE_PATTERN = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s*$")
_MONEY_LINE_PATTERN = re.compile(
    r"^\s*(?:R\$\s*)?(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})\s*$"
)
_HEADER_LINES = frozenset({"DATA", "LANCAMENTO", "CREDITO", "DEBITO", "SALDO"})


@dataclass(frozen=True, slots=True)
class TrustSrmLayoutParser:
    layout_names: frozenset[str] = frozenset({TRUST_SRM_STATEMENT_LAYOUT})

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

        rows = _parse_statement_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_trust_srm_statement",
            selection_reason="layout_specific_trust_srm_statement:multiline_descending_balance",
        )


def _parse_statement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    inside_movements = False
    current_date: str | None = None
    description_parts: list[str] = []
    description_source: _PdfLine | None = None
    pending_amount: float | None = None
    skip_balance_value = False

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized == "MOVIMENTACOES":
            inside_movements = True
            continue
        if not inside_movements:
            continue
        if normalized == "SALDO ANTERIOR":
            break
        if normalized in _HEADER_LINES:
            if normalized == "SALDO" and current_date is not None:
                skip_balance_value = True
                description_parts = []
                description_source = None
                pending_amount = None
            continue

        if _DATE_LINE_PATTERN.fullmatch(line.text):
            current_date = parse_row_date(line.text.strip(), fallback_year=None)
            description_parts = []
            description_source = None
            pending_amount = None
            skip_balance_value = False
            continue
        if current_date is None:
            continue

        money_match = _MONEY_LINE_PATTERN.fullmatch(line.text)
        if money_match is not None:
            value = parse_pdf_amount(money_match.group("amount"))
            if skip_balance_value:
                skip_balance_value = False
                current_date = None
                continue
            if not description_parts:
                continue
            if pending_amount is None:
                pending_amount = value
                continue

            description = " ".join(" ".join(description_parts).split())
            amount = _resolve_amount_sign(pending_amount, description=description)
            source = description_source or line
            rows.append(
                build_parsed_transaction(
                    date=current_date,
                    description=description,
                    amount=amount,
                    source_page=source.page_number,
                    source_line=source.line_number,
                    running_balance=value,
                    has_explicit_amount_sign=True,
                )
            )
            description_parts = []
            description_source = None
            pending_amount = None
            continue

        if not normalized:
            continue
        if description_source is None:
            description_source = line
        description_parts.append(line.text.strip())

    return rows


def _resolve_amount_sign(amount: float, *, description: str) -> float:
    normalized = normalize_text(description)
    if normalized.startswith(("ENVIO ", "PAGAMENTO ", "DEBITO ", "TARIFA ", "SAQUE ")):
        return -abs(amount)
    return abs(amount)
