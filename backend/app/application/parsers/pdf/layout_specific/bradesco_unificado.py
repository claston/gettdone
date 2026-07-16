from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.application.normalization.date import parse_statement_date
from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_row_match_rules import is_amount_only_row, is_date_only_row
from app.application.normalization.pdf_text_rules import apply_sign_hints
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

BRADESCO_UNIFICADO_POUPANCA_LAYOUT = "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1"


@dataclass(frozen=True, slots=True)
class BradescoUnificadoLayoutParser:
    layout_names: frozenset[str] = frozenset({BRADESCO_UNIFICADO_POUPANCA_LAYOUT})

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
        rows = _parse_movimentacao_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_bradesco_unificado_movimentacao",
            selection_reason="layout_specific_bradesco_unificado_movimentacao",
        )


def _parse_movimentacao_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    movement_lines = _slice_movimentacao_lines(lines)
    if not movement_lines:
        return []

    fallback_year = _infer_reference_year(lines) or datetime.now().year
    parsed_rows: list[_ParsedTransaction] = []
    previous_running_balance: float | None = None
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue

        current_date = parse_statement_date(line.text.strip(), fallback_year)
        next_index = index + 1
        description_parts: list[str] = []
        document_value: str | None = None
        amount_value: float | None = None
        running_balance: float | None = None

        while next_index < len(movement_lines):
            current = movement_lines[next_index]
            normalized_current = normalize_text(current.text)
            if normalized_current in {
                "DATA",
                "HISTORICO",
                "HISTÓRICO",
                "DOCUMENTO",
                "INDICES",
                "ÍNDICES",
                "CREDITO",
                "CRÉDITO",
                "DEBITO",
                "DÉBITO",
                "SALDO",
            }:
                next_index += 1
                continue
            if is_date_only_row(current.text):
                break
            if is_amount_only_row(current.text):
                parsed_amount = parse_pdf_amount(current.text)
                if amount_value is None:
                    amount_value = abs(parsed_amount)
                else:
                    running_balance = parsed_amount
                    next_index += 1
                    break
                next_index += 1
                continue

            digits_only = re.sub(r"\D", "", current.text)
            if document_value is None and amount_value is None and re.fullmatch(r"\d{6,8}", digits_only):
                document_value = digits_only
            else:
                description_parts.append(current.text.strip())
            next_index += 1

        description = " ".join(part for part in description_parts if part).strip()
        normalized_description = normalize_text(description)
        if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
            opening_balance = running_balance if running_balance is not None else amount_value
            if opening_balance is not None:
                parsed_rows.append(
                    build_parsed_transaction(
                        date=current_date,
                        description="SALDO ANTERIOR",
                        amount=opening_balance,
                        source_page=line.page_number,
                        source_line=line.line_number,
                        running_balance=opening_balance,
                        has_explicit_amount_sign=has_explicit_amount_sign(str(opening_balance)),
                    )
                )
                previous_running_balance = opening_balance
            index = next_index
            continue

        if description and amount_value is not None:
            full_description = description
            if document_value:
                full_description = f"{full_description} {document_value}".strip()
            signed_amount = _resolve_signed_amount(
                raw_amount=amount_value,
                description=full_description,
                previous_running_balance=previous_running_balance,
                running_balance=running_balance,
            )
            parsed_rows.append(
                build_parsed_transaction(
                    date=current_date,
                    description=full_description,
                    amount=signed_amount,
                    source_page=line.page_number,
                    source_line=line.line_number,
                    running_balance=running_balance,
                    external_reference_id=document_value,
                    has_explicit_amount_sign=amount_value < 0,
                )
            )
            if running_balance is not None:
                previous_running_balance = running_balance
            elif previous_running_balance is not None:
                previous_running_balance = round(previous_running_balance + signed_amount, 2)

        index = next_index

    return parsed_rows


def _slice_movimentacao_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    for index, line in enumerate(lines):
        if normalize_text(line.text) == "DEMONSTRATIVO DA MOVIMENTACAO":
            return lines[index + 1 :]
    return []


def _infer_reference_year(lines: list[_PdfLine]) -> int | None:
    years: list[int] = []
    for line in lines:
        for raw in re.findall(r"\b\d{2}/\d{2}/(\d{4})\b", line.text):
            year = int(raw)
            if 1900 <= year <= 2100:
                years.append(year)
    if years:
        return max(years)
    return infer_default_statement_year_from_lines(lines)


def _resolve_signed_amount(
    *,
    raw_amount: float,
    description: str,
    previous_running_balance: float | None,
    running_balance: float | None,
) -> float:
    amount = abs(raw_amount)
    if previous_running_balance is not None and running_balance is not None:
        if abs((previous_running_balance + amount) - running_balance) <= 0.02:
            return amount
        if abs((previous_running_balance - amount) - running_balance) <= 0.02:
            return -amount
    return apply_sign_hints(amount, description, None)
