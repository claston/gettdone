from __future__ import annotations

import re
from decimal import Decimal

from app.application.errors import InvalidFileContentError
from app.application.models import CanonicalTransaction, TransactionRow
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.text import normalize_upper_text

_DAY_SUMMARY = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+SALDO DO DIA\s+(?P<balance>.+)$")
_TOLERANCE = 0.01


def resolve_bradesco_split_day_order(rows: list[CanonicalTransaction], source_pages: tuple[str, ...]) -> list[int]:
    """Reorder only split daily blocks that reconcile with their printed close."""
    summaries: dict[str, list[tuple[tuple[int, int], float]]] = {}
    for page_number, page in enumerate(source_pages, 1):
        for line_number, text in enumerate(page.splitlines(), 1):
            match = _DAY_SUMMARY.fullmatch(normalize_upper_text(text))
            if match is None:
                continue
            tokens = find_amount_tokens(match["balance"])
            if len(tokens) != 1:
                continue
            try:
                date = parse_row_date(match["date"], fallback_year=None)
                balance = parse_pdf_amount(tokens[0].value)
            except InvalidFileContentError:
                continue
            summaries.setdefault(date, []).append(((page_number, line_number), balance))

    order: list[int] = []
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end].date == rows[start].date:
            end += 1
        group = rows[start:end]
        indices = list(range(len(group)))
        complete = all(row.running_balance is not None and row.source_page is not None and row.source_line is not None for row in group)
        if complete and len(group) > 1 and not _consistent(group, indices):
            positions = [(row.source_page, row.source_line) for row in group]
            interior = [
                (position, value) for position, value in summaries.get(group[0].date, []) if min(positions) < position < max(positions)
            ]
            if interior:
                position, closing = interior[0]
                preceding = max(
                    (row for row in group if (row.source_page, row.source_line) < position),
                    key=lambda row: (row.source_page, row.source_line),
                )
                candidate = _unique_balance_order(group)
                if (
                    candidate is not None
                    and abs(preceding.running_balance - closing) <= _TOLERANCE
                    and _consistent(group, candidate)
                    and abs(group[candidate[-1]].running_balance - closing) <= _TOLERANCE
                ):
                    indices = candidate
        order.extend(start + index for index in indices)
        start = end
    return order


def _consistent(rows: list[CanonicalTransaction], indices: list[int]) -> bool:
    return all(
        abs(rows[current].running_balance - (rows[previous].running_balance + rows[current].amount)) <= _TOLERANCE
        for previous, current in zip(indices, indices[1:])
    )


def _unique_balance_order(rows: list[CanonicalTransaction]) -> list[int] | None:
    balances = [Decimal(str(row.running_balance)) for row in rows]
    openings = [balance - Decimal(str(row.amount)) for balance, row in zip(balances, rows, strict=True)]
    tolerance = Decimal("0.01")
    roots = [
        index
        for index, opening in enumerate(openings)
        if not any(other != index and abs(opening - balance) <= tolerance for other, balance in enumerate(balances))
    ]
    if len(roots) != 1:
        return None
    order = [roots[0]]
    remaining = set(range(len(rows))) - set(order)
    while remaining:
        matches = [index for index in remaining if abs(openings[index] - balances[order[-1]]) <= tolerance]
        if len(matches) != 1:
            return None
        order.append(matches[0])
        remaining.remove(matches[0])
    return order


def resolve_bradesco_period_opening(rows: list[TransactionRow]) -> float | None:
    if not rows:
        return None
    first_date = min(row.date for row in rows)
    day_rows = [row for row in rows if row.date == first_date]
    amounts = 0.0
    for row in day_rows:
        amounts += row.amount
        if row.running_balance is not None:
            return round(row.running_balance - amounts, 2)
    return None


def resolve_bradesco_period_closing(rows: list[TransactionRow]) -> float | None:
    if not rows:
        return None
    last_date = max(row.date for row in rows)
    day_rows = [row for row in rows if row.date == last_date]
    amounts = 0.0
    for row in reversed(day_rows):
        if row.running_balance is not None:
            return round(row.running_balance + amounts, 2)
        amounts += row.amount
    return None
