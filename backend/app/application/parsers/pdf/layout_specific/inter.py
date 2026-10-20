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
from app.application.parsers.pdf.layout_specific.inter_ocr import parse_inter_ocr
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

INTER_MOVEMENTS_LAYOUT = "banco_inter_extrato_conta_corrente_movimentacoes_v1"
INTER_DAILY_LIST_LAYOUT = "banco_inter_extrato_conta_corrente_lista_diaria_v1"
INTER_CREDIT_CARD_LAYOUT = "banco_inter_fatura_cartao_despesas_v1"
INTER_RUNNING_BALANCE_LAYOUT = "banco_inter_extrato_conta_corrente_saldo_transacao_v1"
INTER_FIXED_INCOME_LAYOUT = "banco_inter_extrato_posicao_renda_fixa_v1"

_ALL_LAYOUTS = frozenset(
    {
        INTER_MOVEMENTS_LAYOUT,
        INTER_DAILY_LIST_LAYOUT,
        INTER_CREDIT_CARD_LAYOUT,
        INTER_RUNNING_BALANCE_LAYOUT,
        INTER_FIXED_INCOME_LAYOUT,
    }
)
_DATE_AT_START_PATTERN = re.compile(
    rf"^\s*(?P<date>{STATEMENT_DATE_TOKEN})(?=\s|$)(?P<rest>.*)$",
    flags=re.IGNORECASE,
)
_DATE_PATTERN = re.compile(rf"(?<!\d)(?:{STATEMENT_DATE_TOKEN})(?!\d)", flags=re.IGNORECASE)
_NOTE_PATTERN = re.compile(r"^\s*(?P<note>\d{6,20})\b")
_SUMMARY_TOKENS = (
    "SALDO ATUAL",
    "SALDO DISPONIVEL",
    "SALDO TOTAL",
    "SALDO BLOQUEADO",
    "SALDO DO DIA",
)


@dataclass(frozen=True, slots=True)
class InterLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name == INTER_MOVEMENTS_LAYOUT:
            rows = _parse_date_first_bank_rows(lines)
        elif layout_name == INTER_DAILY_LIST_LAYOUT:
            rows = _parse_grouped_rows(lines, include_running_balance=False)
        elif layout_name == INTER_CREDIT_CARD_LAYOUT:
            rows = _parse_credit_card_rows(lines)
        elif layout_name == INTER_RUNNING_BALANCE_LAYOUT:
            if context.source_extraction is not None:
                return LayoutSpecificParseResult(parse_inter_ocr(context.source_extraction), "layout_specific_inter_ocr",
                                                "inter_ocr_financial_columns_and_all_daily_balances_validated")
            rows = _parse_grouped_rows(lines, include_running_balance=True)
        elif layout_name == INTER_FIXED_INCOME_LAYOUT:
            rows = _parse_fixed_income_rows(lines)
        else:
            return None

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_inter",
            selection_reason=f"layout_specific_inter:{layout_name}",
        )


def _parse_date_first_bank_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = find_amount_tokens(match.group("rest"))
        if not amount_tokens:
            continue
        date = _parse_date(match.group("date"))
        if date is None:
            continue
        amount_token = amount_tokens[0]
        running_balance = parse_pdf_amount(amount_tokens[1].value) if len(amount_tokens) > 1 else None
        description = _clean_description(match.group("rest"), amount_tokens=tuple(amount_tokens))
        if not description or _is_summary(description):
            continue
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=parse_pdf_amount(amount_token.value),
                amount_token=amount_token,
                source=line,
                running_balance=running_balance,
            )
        )
    return rows


def _parse_credit_card_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = find_amount_tokens(match.group("rest"))
        if not amount_tokens:
            continue
        date = _parse_date(match.group("date"))
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(match.group("rest"), amount_tokens=tuple(amount_tokens))
        normalized = normalize_text(description)
        raw_amount = parse_pdf_amount(amount_token.value)
        is_payment = "PAGAMENTO" in normalized or amount_token.value.strip().startswith("+")
        amount = abs(raw_amount) if is_payment else -abs(raw_amount)
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_grouped_rows(lines: list[_PdfLine], *, include_running_balance: bool) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    current_date: str | None = None
    for line in lines:
        date_match = _DATE_AT_START_PATTERN.match(line.text)
        if date_match is not None:
            parsed_date = _parse_date(date_match.group("date"))
            if parsed_date is not None:
                current_date = parsed_date
            continue
        if current_date is None:
            continue
        amount_tokens = find_amount_tokens(line.text)
        if not amount_tokens:
            continue
        description = _clean_description(line.text, amount_tokens=tuple(amount_tokens))
        if not description or _is_summary(description):
            continue
        amount_token = amount_tokens[0]
        running_balance = (
            parse_pdf_amount(amount_tokens[1].value)
            if include_running_balance and len(amount_tokens) > 1
            else None
        )
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount=parse_pdf_amount(amount_token.value),
                amount_token=amount_token,
                source=line,
                running_balance=running_balance,
            )
        )
    return rows


def _parse_fixed_income_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        note_match = _NOTE_PATTERN.match(line.text)
        date_matches = list(_DATE_PATTERN.finditer(line.text))
        amount_tokens = find_amount_tokens(line.text)
        if note_match is None or "CDB" not in normalized or len(date_matches) < 2 or not amount_tokens:
            continue
        date = _parse_date(date_matches[0].group(0))
        if date is None:
            continue
        amount_token = amount_tokens[0]
        note = note_match.group("note")
        rows.append(
            build_parsed_transaction(
                date=date,
                description=f"APLICACAO CDB NOTA {note}",
                amount=-abs(parse_pdf_amount(amount_token.value)),
                source_page=line.page_number,
                source_line=line.line_number,
                external_reference_id=note,
                has_explicit_amount_sign=True,
            )
        )
    return rows


def _build_row(
    *,
    date: str,
    description: str,
    amount: float,
    amount_token: AmountToken,
    source: _PdfLine,
    running_balance: float | None = None,
    forced_sign: bool = False,
) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        running_balance=running_balance,
        has_explicit_amount_sign=forced_sign or has_explicit_amount_sign(amount_token.value),
    )


def _parse_date(raw_date: str) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None


def _is_summary(description: str) -> bool:
    normalized = normalize_text(description)
    return any(token in normalized for token in _SUMMARY_TOKENS)


def _clean_description(raw_text: str, *, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = raw_text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())
