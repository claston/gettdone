from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import AmountToken, find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

SISPRIME_ACCOUNT_STATEMENT_LAYOUT = "sisprime_extrato_conta_debito_credito_saldo_v1"

_DATE_PREFIX_PATTERN = re.compile(r"^\s*(?P<date>\d{2}/\d{2}/\d{4})(?P<rest>\s+.+)$")
_BALANCE_TOLERANCE = 0.011


@dataclass(frozen=True, slots=True)
class _ColumnAnchors:
    document_start: int
    history_start: int
    description_start: int
    debit_end: int
    credit_end: int


@dataclass(frozen=True, slots=True)
class SisprimeLayoutParser:
    layout_names: frozenset[str] = frozenset({SISPRIME_ACCOUNT_STATEMENT_LAYOUT})

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name != SISPRIME_ACCOUNT_STATEMENT_LAYOUT:
            return None

        rows = _parse_statement_rows(lines)
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_sisprime",
            selection_reason=f"layout_specific_sisprime:{layout_name}",
        )


def _parse_statement_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    anchors_by_page = _find_column_anchors(lines)
    rows: list[_ParsedTransaction] = []
    previous_balance: float | None = None

    for line in lines:
        anchors = anchors_by_page.get(line.page_number)
        if anchors is None:
            continue
        parsed = _parse_statement_row(
            line,
            anchors=anchors,
            previous_balance=previous_balance,
        )
        if parsed is None:
            continue
        rows.append(parsed)
        previous_balance = parsed.running_balance
    return rows


def _parse_statement_row(
    line: _PdfLine,
    *,
    anchors: _ColumnAnchors,
    previous_balance: float | None,
) -> _ParsedTransaction | None:
    match = _DATE_PREFIX_PATTERN.match(line.text)
    if match is None:
        return None
    try:
        date = parse_row_date(match.group("date"), fallback_year=None)
    except InvalidFileContentError:
        return None

    amount_tokens = find_amount_tokens(line.text)
    if len(amount_tokens) != 2:
        return None
    movement_token, balance_token = _restore_prefixed_balance_sign(*amount_tokens)
    raw_amount = abs(parse_pdf_amount(movement_token.value))
    running_balance = parse_pdf_amount(balance_token.value)
    amount = _resolve_amount_sign(
        raw_amount,
        movement_token=movement_token,
        anchors=anchors,
        previous_balance=previous_balance,
        running_balance=running_balance,
    )
    document = line.text[anchors.document_start : anchors.history_start].strip() or None
    history = line.text[anchors.history_start : anchors.description_start].strip()
    detail = line.text[anchors.description_start : movement_token.start].strip()
    description = " ".join(part for part in (history, detail) if part)
    description = " ".join(description.split())
    if not description:
        return None

    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=line.page_number,
        source_line=line.line_number,
        running_balance=running_balance,
        external_reference_id=document,
        has_explicit_amount_sign=True,
    )


def _resolve_amount_sign(
    raw_amount: float,
    *,
    movement_token: AmountToken,
    anchors: _ColumnAnchors,
    previous_balance: float | None,
    running_balance: float,
) -> float:
    if previous_balance is not None:
        balance_delta = round(running_balance - previous_balance, 2)
        if abs(abs(balance_delta) - raw_amount) <= _BALANCE_TOLERANCE:
            return abs(raw_amount) if balance_delta >= 0 else -abs(raw_amount)

    debit_distance = abs(movement_token.end - anchors.debit_end)
    credit_distance = abs(movement_token.end - anchors.credit_end)
    return -abs(raw_amount) if debit_distance <= credit_distance else abs(raw_amount)


def _restore_prefixed_balance_sign(
    movement_token: AmountToken,
    balance_token: AmountToken,
) -> tuple[AmountToken, AmountToken]:
    movement_value = movement_token.value.rstrip()
    if not movement_value.endswith(("-", "+", "−")):
        return movement_token, balance_token
    if not balance_token.value.lstrip().upper().startswith(("R$", "US$")):
        return movement_token, balance_token

    sign = movement_value[-1]
    unsigned_movement_value = movement_value[:-1].rstrip()
    return (
        AmountToken(
            value=unsigned_movement_value,
            start=movement_token.start,
            end=movement_token.start + len(unsigned_movement_value),
            role_hint=movement_token.role_hint,
        ),
        AmountToken(
            value=f"{sign}{balance_token.value}",
            start=balance_token.start - 1,
            end=balance_token.end,
            role_hint=balance_token.role_hint,
        ),
    )


def _find_column_anchors(lines: list[_PdfLine]) -> dict[int, _ColumnAnchors]:
    anchors_by_page: dict[int, _ColumnAnchors] = {}
    for line in lines:
        folded = _ascii_fold_upper_preserving_spacing(line.text)
        document_start = folded.find("DOCUMENTO")
        history_start = _find_corrupted_or_ascii_label(folded, r"HIST(?:O|�)RICO")
        description_start = _find_corrupted_or_ascii_label(folded, r"DESCRI(?:C|�)(?:A|�)O")
        debit_start = _find_corrupted_or_ascii_label(folded, r"D(?:E|�)BITO")
        credit_start = _find_corrupted_or_ascii_label(folded, r"CR(?:E|�)DITO")
        balance_start = folded.find("SALDO", credit_start + len("CREDITO"))
        if min(document_start, history_start, description_start, debit_start, credit_start, balance_start) < 0:
            continue
        if not (
            document_start < history_start < description_start < debit_start < credit_start < balance_start
        ):
            continue
        # Collapsed native text loses the visual columns. Waiting for the
        # layout-preserving extraction is safer than guessing from history text.
        if credit_start - debit_start < 10:
            continue
        anchors_by_page[line.page_number] = _ColumnAnchors(
            document_start=document_start,
            history_start=history_start,
            description_start=description_start,
            debit_end=debit_start + len("DEBITO"),
            credit_end=credit_start + len("CREDITO"),
        )
    return anchors_by_page


def _find_corrupted_or_ascii_label(value: str, pattern: str) -> int:
    match = re.search(pattern, value)
    return -1 if match is None else match.start()


def _ascii_fold_upper_preserving_spacing(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.upper())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
