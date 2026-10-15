from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.application.models import NormalizedTransaction
from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BRADESCO_NET_EMPRESA_LAYOUT = "bradesco_net_empresa_extrato_mensal_por_periodo_v1"

_DATE_PREFIX_PATTERN = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})(?P<body>.*)$")
_AMOUNT_PATTERN = re.compile(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}")
_SPACED_DOCUMENT_PATTERN = re.compile(r"^(?=.*\d)[A-Za-z0-9./_-]{1,20}$")
_GLUED_DOCUMENT_PATTERN = re.compile(r"^(?P<description>.*?\D)(?P<document>\d{1,10})$")


@dataclass(frozen=True, slots=True)
class BradescoNetEmpresaLayoutParser:
    layout_names: frozenset[str] = frozenset({BRADESCO_NET_EMPRESA_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name != BRADESCO_NET_EMPRESA_LAYOUT:
            return None

        rows = _parse_statement_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_bradesco_net_empresa",
            selection_reason="layout_specific_bradesco_net_empresa:fixed_credit_debit_balance_rows",
        )


def _parse_statement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    current_date: str | None = None
    last_row_index: int | None = None

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized.startswith("SALDOS INVEST"):
            break
        if normalized.startswith("TOTAL"):
            last_row_index = None
            continue

        date_match = _DATE_PREFIX_PATTERN.match(line.text)
        if date_match is not None:
            current_date = parse_row_date(date_match.group("date"), fallback_year=None)

        amount_matches = tuple(_AMOUNT_PATTERN.finditer(line.text))
        if current_date is not None and len(amount_matches) >= 2:
            amount_match = amount_matches[-2]
            balance_match = amount_matches[-1]
            prefix = line.text[: amount_match.start()]
            description, document = _extract_description_and_document(prefix)
            if description and not _should_skip_description(description):
                raw_amount = amount_match.group(0)
                rows.append(
                    build_parsed_transaction(
                        date=current_date,
                        description=description,
                        amount=parse_pdf_amount(raw_amount),
                        source_page=line.page_number,
                        source_line=line.line_number,
                        running_balance=parse_pdf_amount(balance_match.group(0)),
                        external_reference_id=document,
                        has_explicit_amount_sign=has_explicit_amount_sign(raw_amount),
                    )
                )
                last_row_index = len(rows) - 1
                continue

        if _is_description_continuation(line=line, normalized=normalized, rows=rows, last_row_index=last_row_index):
            rows[last_row_index] = _append_description(rows[last_row_index], line.text.strip())

    return rows


def _extract_description_and_document(prefix: str) -> tuple[str, str | None]:
    without_date = _DATE_PREFIX_PATTERN.sub(lambda match: match.group("body"), prefix, count=1).strip()
    if not without_date:
        return "", None

    parts = [part.strip() for part in re.split(r"\s{2,}", without_date) if part.strip()]
    if len(parts) >= 2 and _SPACED_DOCUMENT_PATTERN.fullmatch(parts[-1]):
        return " ".join(parts), parts[-1]

    glued_match = _GLUED_DOCUMENT_PATTERN.fullmatch(without_date)
    if glued_match is not None:
        document = glued_match.group("document")
        return f"{glued_match.group('description').strip()} {document}", document
    return " ".join(without_date.split()), None


def _should_skip_description(description: str) -> bool:
    normalized = normalize_text(description)
    if normalized.startswith(("SALDO ANTERIOR", "SALDO INICIAL", "SALDO FINAL")):
        return True
    return re.fullmatch(r"\d{1,6}(?:\s*-\s*[0-9X])?\s*\|\s*[0-9. -]+", normalized) is not None


def _is_description_continuation(
    *,
    line: _PdfLine,
    normalized: str,
    rows: list[_ParsedTransaction],
    last_row_index: int | None,
) -> bool:
    if last_row_index is None or last_row_index >= len(rows) or not normalized:
        return False
    if rows[last_row_index].source_page != line.page_number:
        return False
    if not line.text[:1].isspace() or _AMOUNT_PATTERN.search(line.text):
        return False
    return not normalized.startswith(
        (
            "AGENCIA | CONTA",
            "DATA",
            "EXTRATO DE:",
            "FOLHA ",
            "OS DADOS ACIMA",
            "SALDO ANTERIOR",
            "SALDOS INVEST",
            "TOTAL DISPONIVEL",
        )
    )


def _append_description(row: _ParsedTransaction, continuation: str) -> _ParsedTransaction:
    transaction = row.transaction
    return replace(
        row,
        transaction=NormalizedTransaction(
            date=transaction.date,
            description=" ".join(f"{transaction.description} {continuation}".split()),
            amount=transaction.amount,
            type=transaction.type,
        ),
    )
