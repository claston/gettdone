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

CRESOL_LEGACY_CURRENT_LAYOUT = "cresol_extrato_conta_corrente_legado_sinal_sufixo_v1"
CRESOL_CONSOLIDATED_CURRENT_LAYOUT = "cresol_extrato_consolidado_conta_corrente_valor_cd_v1"
CRESOL_DAILY_LIST_LAYOUT = "cresol_extrato_lancamentos_saldo_dia_pix_credito_v1"
CRESOL_RDC_LAYOUT = "cresol_extrato_rdc_renda_fixa_v1"
CRESOL_PIX_DAILY_LIST_LAYOUT = "cresol_extrato_conta_corrente_moderno_pix_v1"
CRESOL_OFX_SIMPLES_LAYOUT = "cresol_ofx_simples_posicao_consolidada_v1"

_DATED_TABLE_LAYOUTS = frozenset(
    {
        CRESOL_LEGACY_CURRENT_LAYOUT,
        CRESOL_CONSOLIDATED_CURRENT_LAYOUT,
        CRESOL_RDC_LAYOUT,
        CRESOL_OFX_SIMPLES_LAYOUT,
    }
)
_GROUPED_LIST_LAYOUTS = frozenset({CRESOL_DAILY_LIST_LAYOUT, CRESOL_PIX_DAILY_LIST_LAYOUT})
_ALL_LAYOUTS = _DATED_TABLE_LAYOUTS | _GROUPED_LIST_LAYOUTS
_DATE_ROW_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_DATE_CONTINUATION_PATTERN = re.compile(r"(?:^|\s)(?:-\s*)?\d{1,2}/\d{1,2}\s*$")
_BALANCE_TOKENS = (
    "SALDO ANTERIOR",
    "SALDO INICIAL",
    "SALDO FINAL",
    "SALDO DO DIA",
    "SALDO EM CONTA",
)


@dataclass(frozen=True, slots=True)
class CresolLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        fallback_year = _resolve_fallback_year(lines, context=context)
        if layout_name in _DATED_TABLE_LAYOUTS:
            rows = _parse_dated_table_rows(
                lines,
                fallback_year=fallback_year,
                merge_date_continuations=layout_name == CRESOL_OFX_SIMPLES_LAYOUT,
            )
        elif layout_name in _GROUPED_LIST_LAYOUTS:
            rows = _parse_grouped_list_rows(lines, fallback_year=fallback_year)
        else:
            return None

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_cresol",
            selection_reason=f"layout_specific_cresol:{layout_name}",
        )


def _parse_dated_table_rows(
    lines: list[_PdfLine],
    *,
    fallback_year: int,
    merge_date_continuations: bool = False,
) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    last_transaction_line: _PdfLine | None = None
    for line in lines:
        match = _DATE_ROW_PATTERN.match(line.text)
        if match is None:
            if (
                merge_date_continuations
                and rows
                and last_transaction_line is not None
                and _is_date_description_continuation(line, previous_line=last_transaction_line)
            ):
                rows[-1] = _append_description(rows[-1], line.text)
                last_transaction_line = line
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(match.group("rest"), amount_token=amount_token)
        if _is_balance_description(description) or not _has_explicit_amount_sign(amount_token):
            continue
        rows.append(
            _build_row(
                date=parse_row_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount_token=amount_token,
                source=line,
            )
        )
        last_transaction_line = line
    return rows


def _is_date_description_continuation(line: _PdfLine, *, previous_line: _PdfLine) -> bool:
    if line.page_number != previous_line.page_number or line.line_number != previous_line.line_number + 1:
        return False
    return _DATE_CONTINUATION_PATTERN.search(line.text.strip()) is not None


def _append_description(row: _ParsedTransaction, continuation: str) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=row.transaction.date,
        description=" ".join(f"{row.transaction.description} {continuation}".split()),
        amount=row.transaction.amount,
        source_page=row.source_page,
        source_line=row.source_line,
        running_balance=row.running_balance,
        external_reference_id=row.external_reference_id,
        has_explicit_amount_sign=row.has_explicit_amount_sign,
    )


def _parse_grouped_list_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    current_date: str | None = None
    inside_transactions = False

    for line in lines:
        normalized_line = normalize_text(line.text)
        if normalized_line == "LANCAMENTOS" or normalized_line.startswith("LANCAMENTOS "):
            inside_transactions = True
            continue
        if not inside_transactions:
            continue

        date_match = _DATE_ROW_PATTERN.match(line.text)
        if date_match is not None:
            current_date = parse_row_date(date_match.group("date"), fallback_year=fallback_year)
            rest = date_match.group("rest")
            if _is_balance_description(rest):
                continue
            amount_tokens = tuple(find_amount_tokens(rest))
            if not amount_tokens:
                continue
            amount_token = amount_tokens[-1]
            if not _has_explicit_amount_sign(amount_token):
                continue
            rows.append(
                _build_row(
                    date=current_date,
                    description=_clean_description(rest, amount_token=amount_token),
                    amount_token=amount_token,
                    source=line,
                )
            )
            continue

        if current_date is None or _is_balance_description(line.text):
            continue
        amount_tokens = tuple(find_amount_tokens(line.text))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        if not _has_explicit_amount_sign(amount_token):
            continue
        description = _clean_description(line.text, amount_token=amount_token)
        if not description:
            continue
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount_token=amount_token,
                source=line,
            )
        )

    return rows


def _build_row(*, date: str, description: str, amount_token: AmountToken, source: _PdfLine) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=parse_amount_token(amount_token),
        source_page=source.page_number,
        source_line=source.line_number,
        has_explicit_amount_sign=True,
    )


def _clean_description(raw_text: str, *, amount_token: AmountToken) -> str:
    without_amount = raw_text[: amount_token.start] + " " + raw_text[amount_token.end :]
    without_currency_tail = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", without_amount, flags=re.IGNORECASE)
    return " ".join(without_currency_tail.strip(" -|:").split())


def _is_balance_description(value: str) -> bool:
    normalized = normalize_text(value)
    return any(token in normalized for token in _BALANCE_TOKENS)


def _has_explicit_amount_sign(amount_token: AmountToken) -> bool:
    return has_amount_token_explicit_sign(amount_token) or bool(
        re.search(r"[CD]\s*$", amount_token.value, flags=re.IGNORECASE)
    )


def _resolve_fallback_year(lines: list[_PdfLine], *, context: LayoutSpecificParseContext) -> int:
    inferred = infer_default_statement_year_from_lines(lines)
    if inferred is not None:
        return inferred
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return datetime.now(timezone.utc).year
