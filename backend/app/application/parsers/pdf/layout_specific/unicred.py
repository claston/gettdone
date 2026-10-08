from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.document_extraction_models import ExtractedLine, ExtractedPage
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
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

UNICRED_MODERN_CURRENT_LAYOUT = "unicred_extrato_conta_corrente_moderno_v1"

_DATE_ROW_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?<![\d,.])(?P<sign>[+\-\u2212]?)\s*R\$\s*"
    r"(?P<number>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?![\d,.])",
    flags=re.IGNORECASE,
)
_CREDIT_DESCRIPTION_TOKENS = (
    "CREDITO",
    "RECEBIMENTO",
    "CRED PIX",
    "DEPOSITO",
    "ESTORNO",
)
_DEBIT_DESCRIPTION_TOKENS = (
    "DEBITO",
    "LIQUIDACAO",
    "PAGAMENTO",
    "TARIFA",
    "JUROS",
    "IOF",
)


@dataclass(frozen=True, slots=True)
class UnicredLayoutParser:
    layout_names: frozenset[str] = frozenset({UNICRED_MODERN_CURRENT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name != UNICRED_MODERN_CURRENT_LAYOUT:
            return None

        fallback_year = _resolve_fallback_year(lines, context=context)
        rows = _parse_transaction_rows(lines, fallback_year=fallback_year)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_unicred",
            selection_reason=f"layout_specific_unicred:{layout_name}",
        )


def parse_unicred_textract_rows(
    pages: list[ExtractedPage],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    reconstructed_lines = [
        row
        for page in pages
        for row in _reconstruct_unicred_page_rows(page)
    ]
    if not reconstructed_lines:
        return []
    fallback_year = _resolve_fallback_year(reconstructed_lines, context=context)
    return _parse_transaction_rows(reconstructed_lines, fallback_year=fallback_year)


def _reconstruct_unicred_page_rows(page: ExtractedPage) -> list[_PdfLine]:
    positioned_lines = [line for line in page.lines if line.text.strip() and _line_position(line) is not None]
    date_anchors = sorted(
        (
            line
            for line in positioned_lines
            if re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{2,4}\s*", line.text)
        ),
        key=lambda line: _line_position(line)[1],
    )
    reconstructed: list[_PdfLine] = []
    for index, anchor in enumerate(date_anchors):
        anchor_top = _line_position(anchor)[1]
        previous_top = _line_position(date_anchors[index - 1])[1] if index > 0 else None
        next_top = _line_position(date_anchors[index + 1])[1] if index + 1 < len(date_anchors) else None
        lower_bound = (previous_top + anchor_top) / 2 if previous_top is not None else anchor_top - 0.025
        upper_bound = (anchor_top + next_top) / 2 if next_top is not None else anchor_top + 0.025
        row_lines = [
            line
            for line in positioned_lines
            if lower_bound <= _line_position(line)[1] < upper_bound
        ]
        currency_lines = sorted(
            (line for line in row_lines if _CURRENCY_AMOUNT_PATTERN.search(line.text)),
            key=lambda line: _line_position(line)[0],
        )
        if len(currency_lines) < 2:
            continue
        amount_line, balance_line = currency_lines[-2:]
        amount_left = _line_position(amount_line)[0]
        description_lines = sorted(
            (
                line
                for line in row_lines
                if line.id != anchor.id
                and line.id not in {amount_line.id, balance_line.id}
                and _line_position(line)[0] < amount_left
            ),
            key=lambda line: (_line_position(line)[1], _line_position(line)[0]),
        )
        description = " ".join(line.text.strip() for line in description_lines if line.text.strip())
        if not description:
            continue
        reconstructed.append(
            _PdfLine(
                text=" ".join(
                    (
                        anchor.text.strip(),
                        description,
                        amount_line.text.strip(),
                        balance_line.text.strip(),
                    )
                ),
                page_number=page.page_number,
                line_number=anchor.line_index,
            )
        )
    return reconstructed


def _line_position(line: ExtractedLine) -> tuple[float, float] | None:
    if line.bbox is None:
        return None
    left = line.bbox.get("left")
    top = line.bbox.get("top")
    if left is None or top is None:
        return None
    return float(left), float(top)


def _parse_transaction_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_ROW_PATTERN.match(line.text)
        if match is None:
            continue

        amount_matches = tuple(_CURRENCY_AMOUNT_PATTERN.finditer(match.group("rest")))
        if len(amount_matches) < 2:
            continue

        amount_match = amount_matches[0]
        balance_match = amount_matches[-1]
        description = _clean_description(match.group("rest"), amount_start=amount_match.start())
        if not description:
            continue

        running_balance = parse_pdf_amount(balance_match.group(0))
        has_explicit_amount_sign = bool(amount_match.group("sign"))
        amount = _resolve_transaction_amount(
            description=description,
            parsed_amount=parse_pdf_amount(amount_match.group(0)),
            has_explicit_sign=has_explicit_amount_sign,
            running_balance=running_balance,
            previous_running_balance=_latest_running_balance(rows),
        )
        rows.append(
            build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=amount,
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                has_explicit_amount_sign=has_explicit_amount_sign,
            )
        )
    return rows


def _resolve_transaction_amount(
    *,
    description: str,
    parsed_amount: float,
    has_explicit_sign: bool,
    running_balance: float,
    previous_running_balance: float | None,
) -> float:
    balance_delta = None
    if previous_running_balance is not None:
        balance_delta = round(running_balance - previous_running_balance, 2)

    if has_explicit_sign:
        return parsed_amount

    normalized_description = normalize_text(description)
    if any(token in normalized_description for token in _CREDIT_DESCRIPTION_TOKENS):
        return abs(parsed_amount)
    if any(token in normalized_description for token in _DEBIT_DESCRIPTION_TOKENS):
        return -abs(parsed_amount)

    if balance_delta is not None:
        if abs(abs(balance_delta) - abs(parsed_amount)) <= 0.01:
            return balance_delta
    return parsed_amount


def _clean_description(rest: str, *, amount_start: int) -> str:
    description = rest[:amount_start]
    return re.sub(r"\s+", " ", description).strip(" -|;")


def _latest_running_balance(rows: list[_ParsedTransaction]) -> float | None:
    for row in reversed(rows):
        if row.running_balance is not None:
            return row.running_balance
    return None


def _resolve_fallback_year(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> int:
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return infer_default_statement_year_from_lines(lines) or datetime.now(timezone.utc).year
