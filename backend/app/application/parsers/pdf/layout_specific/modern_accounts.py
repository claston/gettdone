from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.date import STATEMENT_DATE_TOKEN
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
    normalize_text,
)
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BRADESCO_EMPRESAS_LAYOUT = "bradesco_empresas_negocios_extrato_lancamentos_v1"
SICREDI_MODERN_LAYOUT = "sicredi_extrato_conta_corrente_moderno_movimentacoes_v1"
PICPAY_GROUPED_LAYOUT = "picpay_extrato_conta_grouped_2025_v1"
NEON_MODERN_LAYOUT = "neon_extrato_por_periodo_moderno_v1"
BANCO_BV_GROUPED_LAYOUT = "banco_bv_extrato_periodo_grouped_v1"

_ALL_LAYOUTS = frozenset(
    {
        BRADESCO_EMPRESAS_LAYOUT,
        SICREDI_MODERN_LAYOUT,
        PICPAY_GROUPED_LAYOUT,
        NEON_MODERN_LAYOUT,
        BANCO_BV_GROUPED_LAYOUT,
    }
)
_DATE_AT_START_PATTERN = re.compile(
    rf"^\s*(?P<date>{STATEMENT_DATE_TOKEN})(?=\s|$)(?P<rest>.*)$",
    flags=re.IGNORECASE,
)
_DATE_ANY_PATTERN = re.compile(
    rf"(?<!\d)(?P<date>{STATEMENT_DATE_TOKEN})(?!\d)",
    flags=re.IGNORECASE,
)
_TIME_AT_START_PATTERN = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\s+")
_BRADESCO_DOCUMENT_AND_TYPE_PATTERN = re.compile(
    r"\s+(?P<document>\d{3,})\s+(?:CR[EÉ]DITO|D[EÉ]BITO)\s*$",
    flags=re.IGNORECASE,
)
_SUMMARY_PREFIXES = (
    "SALDO",
    "TOTAL",
    "LIMITE",
    "DISPONIVEL",
    "LANCAMENTOS FUTUROS",
    "MOVIMENTACOES DE",
)
_POSITIVE_DESCRIPTION_HINTS = (
    "CREDITO",
    "PIX RECEBIDO",
    "RECEBIMENTO",
    "DEPOSITO",
    "RESGATE",
    "ESTORNO RECEBIDO",
)
_NEGATIVE_DESCRIPTION_HINTS = (
    "DEBITO",
    "PIX ENVIADO",
    "PAGAMENTO",
    "APLICACAO",
    "TARIFA",
    "ENCARGO",
    "SAIDA",
)


@dataclass(frozen=True, slots=True)
class ModernAccountLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name == BRADESCO_EMPRESAS_LAYOUT:
            rows = _parse_bradesco_rows(lines)
        elif layout_name == SICREDI_MODERN_LAYOUT:
            rows = _parse_sicredi_rows(lines)
        elif layout_name in {PICPAY_GROUPED_LAYOUT, BANCO_BV_GROUPED_LAYOUT}:
            rows = _parse_grouped_long_date_rows(lines)
        elif layout_name == NEON_MODERN_LAYOUT:
            rows = _parse_neon_rows(lines)
        else:
            return None

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_modern_accounts",
            selection_reason=f"layout_specific_modern_accounts:{layout_name}",
        )


def _parse_bradesco_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if len(amount_tokens) < 2:
            continue
        date = _safe_parse_date(match.group("date"))
        if date is None:
            continue
        amount_token, balance_token = amount_tokens[-2], amount_tokens[-1]
        raw_description = _remove_amount_tokens(match.group("rest"), amount_tokens)
        document_match = _BRADESCO_DOCUMENT_AND_TYPE_PATTERN.search(raw_description)
        external_reference_id = document_match.group("document") if document_match is not None else None
        description = (
            raw_description[: document_match.start()].strip()
            if document_match is not None
            else raw_description
        )
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(
            amount_token,
            description=raw_description,
        )
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                running_balance=parse_pdf_amount(balance_token.value),
                external_reference_id=external_reference_id,
                forced_sign=forced_sign,
            )
        )
    return rows


def _parse_sicredi_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    inside_movements = False
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        if normalized.startswith("MOVIMENTACOES DE"):
            inside_movements = True
            continue
        if not inside_movements:
            continue
        if normalized.startswith(("CHEQUE ESPECIAL", "FIM DESSE EXTRATO")):
            break
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if not amount_tokens:
            continue
        date = _safe_parse_date(match.group("date"))
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        description = _remove_amount_tokens(match.group("rest"), amount_tokens)
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=forced_sign,
            )
        )
    return rows


def _parse_grouped_long_date_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    current_date: str | None = None
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        date_match = _DATE_AT_START_PATTERN.match(line.text)
        if date_match is not None and (
            not date_match.group("rest").strip() or "SALDO AO FINAL DO DIA" in normalized
        ):
            current_date = _safe_parse_date(date_match.group("date"))
            continue
        if current_date is None or _TIME_AT_START_PATTERN.match(line.text) is None:
            continue
        without_time = _TIME_AT_START_PATTERN.sub("", line.text, count=1)
        amount_tokens = tuple(find_amount_tokens(without_time))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        description = _remove_amount_tokens(without_time, amount_tokens)
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=forced_sign,
            )
        )
    return rows


def _parse_neon_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        date_match = _DATE_ANY_PATTERN.search(line.text)
        if date_match is None:
            continue
        date = _safe_parse_date(date_match.group("date"))
        if date is None:
            continue
        tail = line.text[date_match.end() :]
        tail_without_empty_card = re.sub(r"\s+-\s*$", "", tail)
        amount_tokens = tuple(find_amount_tokens(tail_without_empty_card))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[0]
        balance_token = amount_tokens[1] if len(amount_tokens) > 1 else None
        description = " ".join(line.text[: date_match.start()].strip(" -|:").split())
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                running_balance=(
                    parse_pdf_amount(balance_token.value) if balance_token is not None else None
                ),
                forced_sign=forced_sign,
            )
        )
    return rows


def _resolve_signed_amount(amount_token: AmountToken, *, description: str) -> tuple[float, bool]:
    raw_amount = parse_pdf_amount(amount_token.value)
    if has_explicit_amount_sign(amount_token.value):
        return raw_amount, False

    normalized_description = normalize_text(description)
    if any(token in normalized_description for token in _NEGATIVE_DESCRIPTION_HINTS):
        return -abs(raw_amount), True
    if any(token in normalized_description for token in _POSITIVE_DESCRIPTION_HINTS):
        return abs(raw_amount), True
    return raw_amount, False


def _remove_amount_tokens(text: str, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())


def _should_skip_description(description: str) -> bool:
    normalized = normalize_text(description)
    return not normalized or normalized.startswith(_SUMMARY_PREFIXES)


def _safe_parse_date(raw_date: str) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None


def _build_row(
    *,
    date: str,
    description: str,
    amount: float,
    amount_token: AmountToken,
    source: _PdfLine,
    running_balance: float | None = None,
    external_reference_id: str | None = None,
    forced_sign: bool = False,
) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
        has_explicit_amount_sign=forced_sign or has_explicit_amount_sign(amount_token.value),
    )
