from __future__ import annotations

import re
from dataclasses import dataclass

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

STONE_CURRENT_ACCOUNT_A4_LAYOUT = "stone_extrato_conta_corrente_a4_v1"

_DATE_PREFIX_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))(?P<rest>(?:\s+.*)?)$"
)
_TABLE_HEADER_WORDS = frozenset({"DATA", "TIPO", "DESCRICAO", "VALOR", "SALDO", "CONTRAPARTE"})


@dataclass(frozen=True, slots=True)
class StoneLayoutParser:
    layout_names: frozenset[str] = frozenset({STONE_CURRENT_ACCOUNT_A4_LAYOUT})

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
            selected_parser="layout_specific_stone_statement",
            selection_reason="layout_specific_stone_statement:multiline_type_amount_balance",
        )


def _parse_statement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    inferred_year = infer_default_statement_year_from_lines(lines)
    current_date: str | None = None
    source: _PdfLine | None = None
    description_parts: list[str] = []
    pending_amount: float | None = None

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized.startswith("INFORMACOES DO COMPROVANTE"):
            _append_pending_without_balance(
                rows,
                date=current_date,
                description_parts=description_parts,
                amount=pending_amount,
                source=source,
            )
            break
        if _is_table_header(normalized) or _is_page_metadata(normalized):
            continue

        date_match = _DATE_PREFIX_PATTERN.match(line.text)
        if date_match is not None:
            _append_pending_without_balance(
                rows,
                date=current_date,
                description_parts=description_parts,
                amount=pending_amount,
                source=source,
            )
            current_date = parse_row_date(date_match.group("date"), fallback_year=inferred_year)
            source = line
            description_parts = []
            pending_amount = None
            remainder = date_match.group("rest").strip()
            if remainder:
                pending_amount = _consume_row_text(
                    remainder,
                    rows=rows,
                    date=current_date,
                    description_parts=description_parts,
                    pending_amount=pending_amount,
                    source=source,
                )
                if rows and rows[-1].source_page == source.page_number and rows[-1].source_line == source.line_number:
                    current_date = None
                    source = None
                    description_parts = []
                    pending_amount = None
            continue

        if current_date is None or not normalized:
            continue
        previous_count = len(rows)
        pending_amount = _consume_row_text(
            line.text.strip(),
            rows=rows,
            date=current_date,
            description_parts=description_parts,
            pending_amount=pending_amount,
            source=source or line,
        )
        if len(rows) > previous_count:
            current_date = None
            source = None
            description_parts = []
            pending_amount = None

    _append_pending_without_balance(
        rows,
        date=current_date,
        description_parts=description_parts,
        amount=pending_amount,
        source=source,
    )
    return rows


def _consume_row_text(
    text: str,
    *,
    rows: list[_ParsedTransaction],
    date: str,
    description_parts: list[str],
    pending_amount: float | None,
    source: _PdfLine,
) -> float | None:
    amount_tokens = find_amount_tokens(text)
    if not amount_tokens:
        description_parts.append(text)
        return pending_amount

    first_token = amount_tokens[0]
    description_prefix = _clean_description_piece(text[: first_token.start])
    if description_prefix:
        description_parts.append(description_prefix)

    if pending_amount is None:
        pending_amount = parse_pdf_amount(first_token.value)
        balance_token = amount_tokens[1] if len(amount_tokens) >= 2 else None
    else:
        balance_token = first_token

    if balance_token is None:
        return pending_amount

    _append_row(
        rows,
        date=date,
        description_parts=description_parts,
        amount=pending_amount,
        running_balance=parse_pdf_amount(balance_token.value),
        source=source,
    )
    return None


def _append_pending_without_balance(
    rows: list[_ParsedTransaction],
    *,
    date: str | None,
    description_parts: list[str],
    amount: float | None,
    source: _PdfLine | None,
) -> None:
    if date is None or amount is None or source is None or not description_parts:
        return
    _append_row(
        rows,
        date=date,
        description_parts=description_parts,
        amount=amount,
        running_balance=None,
        source=source,
    )


def _append_row(
    rows: list[_ParsedTransaction],
    *,
    date: str,
    description_parts: list[str],
    amount: float,
    running_balance: float | None,
    source: _PdfLine,
) -> None:
    description = " ".join(" ".join(description_parts).split())
    if not description:
        return
    rows.append(
        build_parsed_transaction(
            date=date,
            description=description,
            amount=_resolve_amount_sign(amount, description=description),
            source_page=source.page_number,
            source_line=source.line_number,
            running_balance=running_balance,
            has_explicit_amount_sign=True,
        )
    )


def _resolve_amount_sign(amount: float, *, description: str) -> float:
    normalized = normalize_text(description)
    if normalized.startswith("SAIDA"):
        return -abs(amount)
    if normalized.startswith("ENTRADA"):
        return abs(amount)
    return amount


def _clean_description_piece(value: str) -> str:
    return re.sub(r"\s+-\s*$", "", value).strip()


def _is_table_header(normalized: str) -> bool:
    words = frozenset(normalized.split())
    return bool(words) and words <= _TABLE_HEADER_WORDS


def _is_page_metadata(normalized: str) -> bool:
    return normalized.startswith(
        (
            "EXTRATO DE CONTA CORRENTE",
            "STONE INSTITUICAO DE PAGAMENTO",
            "PERIODO:",
            "EMITIDO EM ",
            "PAGINA ",
            "DADOS DA CONTA",
        )
    ) or normalized in {"STONE", "NOME", "DOCUMENTO", "INSTITUICAO", "AGENCIA", "CONTA"}
