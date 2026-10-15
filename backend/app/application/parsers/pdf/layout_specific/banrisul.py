from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.application.normalization.date import MONTH_TO_NUMBER
from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    find_amount_tokens,
    has_explicit_amount_sign,
    parse_pdf_amount,
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

BANRISUL_MONOSPACE_LAYOUT = "banrisul_extrato_texto_movimentos_conta_corrente_v1"
BANRISUL_OPERATIONS_LAYOUT = "banrisul_consulta_operacoes_recibos_v1"
BANRISUL_PIX_LAYOUT = "banrisul_operacoes_pix_v1"
BANRISUL_PAYMENT_RECEIPT_LAYOUT = "banrisul_recibo_pagamento_v1"
BANRISUL_CDB_LAYOUT = "banrisul_demonstrativo_cdb_automatico_v1"
BANRISUL_CARD_HISTORY_LAYOUT = "banrisul_fatura_cartao_historico_transacoes_v1"
BANRISUL_CARD_SIMPLE_LAYOUT = "banrisul_extrato_cartao_credito_simples_v1"

_ALL_LAYOUTS = frozenset(
    {
        BANRISUL_MONOSPACE_LAYOUT,
        BANRISUL_OPERATIONS_LAYOUT,
        BANRISUL_PIX_LAYOUT,
        BANRISUL_PAYMENT_RECEIPT_LAYOUT,
        BANRISUL_CDB_LAYOUT,
        BANRISUL_CARD_HISTORY_LAYOUT,
        BANRISUL_CARD_SIMPLE_LAYOUT,
    }
)

_MONTH_SECTION_PATTERN = re.compile(
    r"\bMOVIMENTOS\s+(?P<month>JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)"
    r"\s*/\s*(?P<year>\d{4})\b"
)
_DAY_PATTERN = re.compile(r"^(?P<day>0[1-9]|[12]\d|3[01])$")
_DOCUMENT_TOKEN = r"(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,20}"
_DOCUMENT_PATTERN = re.compile(rf"^{_DOCUMENT_TOKEN}$")
_AMOUNT_TOKEN = r"\.?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}[+-]?"
_AMOUNT_ONLY_PATTERN = re.compile(rf"^(?P<amount>{_AMOUNT_TOKEN})$")
_FIXED_WIDTH_ROW_PATTERN = re.compile(
    rf"^\s*(?:(?P<day>0[1-9]|[12]\d|3[01])\s+)?"
    rf"(?P<description>.+?)\s+(?P<document>{_DOCUMENT_TOKEN})\s+"
    rf"(?P<amount>{_AMOUNT_TOKEN})\s*$"
)
_IGNORED_EXACT_LINES = {
    "DIA HISTORICO",
    "DOCUMENTO",
    "VALOR",
    "DIA HISTORICO DOCUMENTO VALOR",
}


@dataclass(frozen=True, slots=True)
class BanrisulLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name == BANRISUL_MONOSPACE_LAYOUT:
            rows = _parse_monospace_rows(lines, context=context)
        elif layout_name == BANRISUL_OPERATIONS_LAYOUT:
            rows = _parse_operation_receipt_rows(lines)
        elif layout_name == BANRISUL_PIX_LAYOUT:
            rows = _parse_pix_rows(lines)
        elif layout_name == BANRISUL_PAYMENT_RECEIPT_LAYOUT:
            rows = _parse_payment_receipt(lines)
        elif layout_name == BANRISUL_CDB_LAYOUT:
            rows = _parse_cdb_rows(lines)
        elif layout_name in {BANRISUL_CARD_HISTORY_LAYOUT, BANRISUL_CARD_SIMPLE_LAYOUT}:
            rows = _parse_credit_card_rows(lines, context=context)
        else:
            return None
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser=(
                "layout_specific_banrisul_monospace"
                if layout_name == BANRISUL_MONOSPACE_LAYOUT
                else "layout_specific_banrisul"
            ),
            selection_reason=f"layout_specific_banrisul:{layout_name}",
        )


def _parse_monospace_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    in_movement_table = False
    reference_month_year = context.reference_month_year
    current_day: int | None = None
    pending_description_parts: list[str] = []
    pending_document: str | None = None
    pending_source: _PdfLine | None = None
    parsed_rows: list[_ParsedTransaction] = []

    for line in lines:
        normalized = normalize_text(line.text)
        if "MOVIMENTOS DA CONTA CORRENTE" in normalized:
            in_movement_table = True
            continue
        if not in_movement_table:
            continue

        month_match = _MONTH_SECTION_PATTERN.search(normalized)
        if month_match is not None:
            reference_month_year = (
                MONTH_TO_NUMBER[month_match.group("month")],
                int(month_match.group("year")),
            )
            current_day = None
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        if _should_ignore_line(normalized):
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        fixed_width_match = _FIXED_WIDTH_ROW_PATTERN.fullmatch(line.text)
        if fixed_width_match is not None and reference_month_year is not None:
            raw_day = fixed_width_match.group("day")
            if raw_day is not None:
                current_day = int(raw_day)
            if current_day is None:
                continue
            parsed_rows.append(
                _build_row(
                    day=current_day,
                    reference_month_year=reference_month_year,
                    description=fixed_width_match.group("description"),
                    document=fixed_width_match.group("document"),
                    raw_amount=fixed_width_match.group("amount"),
                    source=line,
                )
            )
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        day_match = _DAY_PATTERN.fullmatch(line.text.strip())
        if day_match is not None:
            current_day = int(day_match.group("day"))
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        amount_match = _AMOUNT_ONLY_PATTERN.fullmatch(line.text.strip())
        if amount_match is not None:
            if (
                current_day is not None
                and reference_month_year is not None
                and pending_description_parts
                and pending_source is not None
            ):
                parsed_rows.append(
                    _build_row(
                        day=current_day,
                        reference_month_year=reference_month_year,
                        description=" ".join(pending_description_parts),
                        document=pending_document,
                        raw_amount=amount_match.group("amount"),
                        source=pending_source,
                    )
                )
            pending_description_parts = []
            pending_document = None
            pending_source = None
            continue

        stripped = line.text.strip()
        if _DOCUMENT_PATTERN.fullmatch(stripped) and pending_description_parts and pending_document is None:
            pending_document = stripped
            continue

        if current_day is None or reference_month_year is None:
            continue
        if pending_source is None:
            pending_source = line
        pending_description_parts.append(stripped)

    return parsed_rows


def _should_ignore_line(normalized: str) -> bool:
    if normalized in _IGNORED_EXACT_LINES:
        return True
    if normalized.startswith(("SALDO ANT", "SALDO ANTERIOR", "SALDO FINAL")):
        return True
    compact = normalized.replace(" ", "")
    return bool(compact) and set(compact) <= {"+", "-"}


def _build_row(
    *,
    day: int,
    reference_month_year: tuple[int, int],
    description: str,
    document: str | None,
    raw_amount: str,
    source: _PdfLine,
) -> _ParsedTransaction:
    month, year = reference_month_year
    date = datetime(year, month, day).strftime("%Y-%m-%d")
    normalized_amount = _normalize_leading_dot_amount(raw_amount)
    amount = parse_pdf_amount(normalized_amount)
    return build_parsed_transaction(
        date=date,
        description=" ".join(description.split()),
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        external_reference_id=document,
        has_explicit_amount_sign=has_explicit_amount_sign(normalized_amount),
    )


def _normalize_leading_dot_amount(raw_amount: str) -> str:
    value = raw_amount.strip()
    if re.fullmatch(r"\.\d{1,3},\d{2}[+-]?", value):
        return value[1:]
    return value


def _parse_operation_receipt_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<rest>.+)$", line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        rest_without_amount = _remove_amount_tokens(match.group("rest"), amount_tokens=(amount_token,))
        reference_match = re.search(r"\b\d{8,20}\b", rest_without_amount)
        operation = "TRANSFERENCIA"
        if reference_match is not None:
            trailing = rest_without_amount[reference_match.end() :]
            operation_match = re.search(r"\b(TRANSFER[ÊE]NCIA|PAGAMENTO|CR[ÉE]DITO|D[ÉE]BITO)\b", trailing, re.I)
            if operation_match is not None:
                operation = operation_match.group(1)
        complement = lines[index + 1].text.strip() if index + 1 < len(lines) else ""
        normalized_complement = normalize_text(complement)
        raw_amount = parse_pdf_amount(amount_token.value)
        if "DEBITO" in normalized_complement:
            amount = -abs(raw_amount)
        elif "CREDITO" in normalized_complement:
            amount = abs(raw_amount)
        else:
            amount = raw_amount
        description = " ".join(f"{operation} {complement}".strip().split())
        rows.append(
            build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=None),
                description=description,
                amount=amount,
                external_reference_id=reference_match.group(0) if reference_match is not None else None,
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _parse_pix_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        operation_match = re.search(r"\bPIX\s+(RECEBIDO|ENVIADO)\b", normalized)
        date_match = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", line.text)
        amount_token = _find_trailing_amount_token(line.text)
        if operation_match is None or date_match is None or amount_token is None:
            continue
        amount = abs(parse_pdf_amount(amount_token.value))
        if operation_match.group(1) == "ENVIADO":
            amount = -amount
        description = _remove_amount_tokens(line.text, amount_tokens=(amount_token,))
        description = description.replace(date_match.group(0), " ")
        rows.append(
            build_parsed_transaction(
                date=parse_row_date(date_match.group(0), fallback_year=None),
                description=" ".join(description.split()),
                amount=amount,
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _parse_payment_receipt(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    full_text = " ".join(line.text.strip() for line in lines if line.text.strip())
    normalized = normalize_text(full_text)
    date_match = re.search(r"DATA DEBITO\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", normalized)
    value_match = re.search(r"\bVALOR\s*:\s*R\$\s*([\d.]+,\d{2})", normalized)
    interest_match = re.search(r"VALOR JUROS\s*:\s*R\$\s*([\d.]+,\d{2})", normalized)
    if date_match is None or value_match is None:
        return []
    value = abs(parse_pdf_amount(value_match.group(1)))
    interest = abs(parse_pdf_amount(interest_match.group(1))) if interest_match is not None else 0.0
    issuer_match = re.search(r"EMISSOR\s*:\s*(.+?)\s+AG\.?/CONTA", normalized)
    description = "PAGAMENTO DE TITULO"
    if issuer_match is not None:
        description = f"{description} {issuer_match.group(1).strip()}"
    source = lines[0] if lines else None
    return [
        build_parsed_transaction(
            date=parse_row_date(date_match.group(1), fallback_year=None),
            description=description,
            amount=-round(value + interest, 2),
            source_page=source.page_number if source is not None else None,
            source_line=source.line_number if source is not None else None,
            has_explicit_amount_sign=True,
        )
    ]


def _parse_cdb_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    history_pattern = re.compile(r"\b(?P<history>APLICA(?:C|Ç)AO|RESGATE)\b", flags=re.IGNORECASE)
    date_pattern = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
    for line in lines:
        history_match = history_pattern.search(line.text)
        if history_match is None:
            continue
        date_matches = [item for item in date_pattern.finditer(line.text) if item.start() < history_match.start()]
        if not date_matches:
            continue
        amount_tokens = tuple(find_amount_tokens(line.text[history_match.end() :]))
        if not amount_tokens:
            continue
        normalized_history = normalize_text(history_match.group("history"))
        amount_token = amount_tokens[-1] if normalized_history == "RESGATE" else amount_tokens[0]
        raw_amount = abs(parse_pdf_amount(amount_token.value))
        amount = raw_amount if normalized_history == "RESGATE" else -raw_amount
        rows.append(
            build_parsed_transaction(
                date=parse_row_date(date_matches[-1].group(0), fallback_year=None),
                description=f"{normalized_history} CDB AUTOMATICO",
                amount=amount,
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _parse_credit_card_rows(
    lines: list[_PdfLine],
    *,
    context: LayoutSpecificParseContext,
) -> list[_ParsedTransaction]:
    fallback_year = _infer_card_year(lines, context=context)
    rows: list[_ParsedTransaction] = []
    date_pattern = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{4})?)\s+(?P<rest>.+)$")
    for line in lines:
        match = date_pattern.match(line.text)
        if match is None:
            continue
        amount_token = _find_trailing_amount_token(match.group("rest"))
        if amount_token is None:
            continue
        description = _remove_amount_tokens(match.group("rest"), amount_tokens=(amount_token,))
        normalized_description = normalize_text(description)
        if "TOTAL DE GASTOS" in normalized_description:
            continue
        raw_amount = parse_pdf_amount(amount_token.value)
        is_payment = "PAGAMENTO" in normalized_description or "PGTO" in normalized_description
        amount = abs(raw_amount) if is_payment or raw_amount < 0 else -abs(raw_amount)
        rows.append(
            build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=amount,
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _infer_card_year(lines: list[_PdfLine], *, context: LayoutSpecificParseContext) -> int | None:
    full_text = normalize_text(" ".join(line.text for line in lines))
    reference_match = re.search(
        r"\b(?:JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)"
        r"\s*/\s*(\d{4})\b",
        full_text,
    )
    if reference_match is not None:
        return int(reference_match.group(1))
    inferred = infer_default_statement_year_from_lines(lines)
    if inferred is not None:
        return inferred
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return None


def _find_trailing_amount_token(value: str) -> AmountToken | None:
    match = re.search(
        r"(?P<amount>[+\-]?\s*(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}[+\-]?)\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))


def _remove_amount_tokens(raw_text: str, *, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = raw_text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())
