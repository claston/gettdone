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
from app.application.normalization.pdf_signed_amount_rules import compute_hint_signed_amount
from app.application.normalization.pdf_text_rules import should_ignore_line, should_skip_transaction_description
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

SANTANDER_STATEMENT_LAYOUT = "santander_statement_ptbr"

_DATE_ROW_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_FULL_DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_DOCUMENT_PATTERN = re.compile(
    r"(?:^|\s)(?P<document>(?=[A-Za-z0-9./_-]*\d)[A-Za-z0-9./_-]{3,})\s*$"
)


@dataclass(frozen=True, slots=True)
class SantanderStatementLayoutParser:
    layout_names: frozenset[str] = frozenset({SANTANDER_STATEMENT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != SANTANDER_STATEMENT_LAYOUT:
            return None

        rows = _parse_movement_rows(lines, context=context)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_santander_statement",
            selection_reason="layout_specific_santander_statement:grouped_daily_movements",
        )


def _parse_movement_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    fallback_year = _resolve_fallback_year(lines, context=context)
    inside_movements = False
    current_date: str | None = None
    rows: list[_ParsedTransaction] = []
    last_content_page: int | None = None
    last_content_line: int | None = None

    for line in lines:
        normalized_line = normalize_text(line.text)
        if _is_movement_heading(normalized_line):
            inside_movements = True
            current_date = None
            continue
        if not inside_movements:
            continue
        if _is_table_header(normalized_line):
            continue
        if normalized_line.startswith("SALDO EM"):
            if rows:
                break
            continue

        date_match = _DATE_ROW_PATTERN.match(line.text)
        body = line.text.strip()
        if date_match is not None:
            current_date = parse_row_date(date_match.group("date"), fallback_year=fallback_year)
            body = date_match.group("rest").strip()

        amount_tokens = tuple(find_amount_tokens(body))
        if not amount_tokens:
            if _is_description_continuation(
                line=line,
                normalized_line=normalized_line,
                rows=rows,
                last_content_page=last_content_page,
                last_content_line=last_content_line,
            ):
                rows[-1] = _append_description(rows[-1], body)
                last_content_line = line.line_number
            continue
        if current_date is None:
            continue

        amount_token, balance_token = _select_amount_and_balance(amount_tokens)
        description, external_reference_id = _extract_description_and_reference(
            body,
            amount_token=amount_token,
        )
        if _should_skip_row(description):
            continue

        raw_amount = parse_amount_token(amount_token)
        amount = compute_hint_signed_amount(raw_amount=raw_amount, description=description)
        running_balance = parse_amount_token(balance_token) if balance_token is not None else None
        rows.append(
            build_parsed_transaction(
                date=current_date,
                description=description,
                amount=amount,
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                external_reference_id=external_reference_id,
                has_explicit_amount_sign=has_amount_token_explicit_sign(amount_token),
            )
        )
        last_content_page = line.page_number
        last_content_line = line.line_number

    return rows


def _select_amount_and_balance(amount_tokens: tuple[AmountToken, ...]) -> tuple[AmountToken, AmountToken | None]:
    if len(amount_tokens) == 1:
        return amount_tokens[0], None
    return amount_tokens[-2], amount_tokens[-1]


def _extract_description_and_reference(
    body: str,
    *,
    amount_token: AmountToken,
) -> tuple[str, str | None]:
    prefix = " ".join(body[: amount_token.start].strip(" -|:").split())
    document_match = _DOCUMENT_PATTERN.search(prefix)
    if document_match is None:
        return prefix, None

    description = prefix[: document_match.start("document")].strip(" -|:")
    return " ".join(description.split()), document_match.group("document")


def _should_skip_row(description: str) -> bool:
    normalized = normalize_text(description)
    if not normalized:
        return True
    if normalized.startswith("TOTAL") or normalized.startswith("SALDO"):
        return True
    return should_skip_transaction_description(description)


def _is_movement_heading(normalized_line: str) -> bool:
    return normalized_line == "MOVIMENTACAO" or normalized_line.startswith("MOVIMENTACAO ")


def _is_table_header(normalized_line: str) -> bool:
    return "DATA" in normalized_line and "DESCRICAO" in normalized_line and "SALDO" in normalized_line


def _is_description_continuation(
    *,
    line: _PdfLine,
    normalized_line: str,
    rows: list[_ParsedTransaction],
    last_content_page: int | None,
    last_content_line: int | None,
) -> bool:
    if not rows or last_content_page != line.page_number or last_content_line is None:
        return False
    if line.line_number != last_content_line + 1:
        return False
    if not normalized_line or should_ignore_line(normalized_line):
        return False
    if _is_table_header(normalized_line) or _is_movement_heading(normalized_line):
        return False
    if normalized_line.startswith(("EXTRATO", "PAGINA", "BANCO SANTANDER", "AGENCIA", "CONTA CORRENTE")):
        return False
    if _FULL_DATE_PATTERN.search(line.text):
        return False
    return not should_skip_transaction_description(line.text)


def _append_description(row: _ParsedTransaction, continuation: str) -> _ParsedTransaction:
    description = " ".join(f"{row.transaction.description} {continuation}".split())
    return build_parsed_transaction(
        date=row.transaction.date,
        description=description,
        amount=row.transaction.amount,
        source_page=row.source_page,
        source_line=row.source_line,
        running_balance=row.running_balance,
        external_reference_id=row.external_reference_id,
        has_explicit_amount_sign=row.has_explicit_amount_sign,
    )


def _resolve_fallback_year(lines: list[_PdfLine], *, context: LayoutSpecificParseContext) -> int:
    inferred = infer_default_statement_year_from_lines(lines)
    if inferred is not None:
        return inferred
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return datetime.now(timezone.utc).year
