from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.pdf_row_match_rules import is_amount_only_row, is_date_only_row
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

SANTANDER_CREDIT_CARD_INVOICE_LAYOUT = "santander_cartao_credito_detalhamento_fatura_paisagem_v1"

_SECTION_HEADERS = {
    "PAGAMENTO E DEMAIS CREDITOS",
    "PARCELAMENTOS",
    "DESPESAS",
}
_NOISE_LINES = {
    "COMPRA",
    "DATA",
    "DESCRICAO",
    "DESCRIÇÃO",
    "PARCELA",
    "R$",
    "US$",
    ".",
    "·",
    ")))",
}


@dataclass(frozen=True, slots=True)
class SantanderCreditCardLayoutParser:
    layout_names: frozenset[str] = frozenset({SANTANDER_CREDIT_CARD_INVOICE_LAYOUT})

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
        rows = _parse_invoice_sections(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="sectioned_credit_card_invoice",
            selection_reason="layout_specific_sectioned_credit_card_invoice",
        )


def _parse_invoice_sections(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    fallback_year = infer_default_statement_year_from_lines(lines) or datetime.now().year
    payment_lines: list[_PdfLine] = []
    installment_lines: list[_PdfLine] = []
    expense_lines: list[_PdfLine] = []
    current_section: str | None = None

    for line in lines:
        normalized = normalize_text(line.text)
        if normalized in _SECTION_HEADERS:
            current_section = normalized
            continue
        if current_section == "PAGAMENTO E DEMAIS CREDITOS":
            payment_lines.append(line)
        elif current_section == "PARCELAMENTOS":
            installment_lines.append(line)
        elif current_section == "DESPESAS":
            expense_lines.append(line)

    return [
        *_parse_payment_rows(payment_lines, fallback_year=fallback_year),
        *_parse_installment_rows(installment_lines, fallback_year=fallback_year),
        *_parse_expense_rows(expense_lines, fallback_year=fallback_year),
    ]


def _parse_payment_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description_parts: list[str] = []
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            if is_date_only_row(current.text):
                break
            if _is_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            description_parts.append(current.text.strip())
            next_index += 1
        description = " ".join(part for part in description_parts if part).strip()
        if description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_invoice_rows(staged_rows, fallback_year=fallback_year)


def _parse_installment_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description = ""
        installment = ""
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            current_text = current.text.strip()
            if re.fullmatch(r"\d{2}/\d{2}", current_text):
                if description and not installment:
                    installment = current_text
                    next_index += 1
                    continue
                break
            if is_date_only_row(current.text):
                break
            if _is_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = -abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            if not description:
                description = current_text
            next_index += 1
        full_description = f"{description} PARCELA {installment}" if description and installment else description
        if full_description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, full_description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_invoice_rows(staged_rows, fallback_year=fallback_year)


def _parse_expense_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description = ""
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            if is_date_only_row(current.text):
                break
            if _is_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = -abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            if not description:
                description = current.text.strip()
            next_index += 1
        if description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_invoice_rows(staged_rows, fallback_year=fallback_year)


def _build_invoice_rows(
    staged_rows: list[tuple[str, str, float, int, int]],
    *,
    fallback_year: int,
) -> list[_ParsedTransaction]:
    if not staged_rows:
        return []
    dated_rows = _resolve_section_dates([row[0] for row in staged_rows], fallback_year=fallback_year)
    parsed_rows: list[_ParsedTransaction] = []
    for (_, description, amount, page_number, source_line), resolved_date in zip(
        staged_rows,
        dated_rows,
        strict=False,
    ):
        parsed_rows.append(
            build_parsed_transaction(
                date=resolved_date,
                description=description,
                amount=amount,
                source_page=page_number,
                source_line=source_line,
                has_explicit_amount_sign=True,
            )
        )
    return parsed_rows


def _resolve_section_dates(raw_dates: list[str], *, fallback_year: int) -> list[str]:
    if not raw_dates:
        return []
    resolved_dates: list[str] = []
    current_year = fallback_year
    current_month: int | None = None
    for raw_date in reversed(raw_dates):
        day, month = [int(part) for part in raw_date.split("/", 1)]
        if current_month is not None and month > current_month:
            current_year -= 1
        resolved_dates.append(f"{current_year:04d}-{month:02d}-{day:02d}")
        current_month = month
    return list(reversed(resolved_dates))


def _is_noise_line(raw_text: str) -> bool:
    normalized = normalize_text(raw_text)
    return normalized in _NOISE_LINES or not normalized
