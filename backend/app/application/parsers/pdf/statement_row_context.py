"""Printed context resolves row dates/order; controls never invent cash movements."""

import re
from datetime import datetime
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.document_period import unique_document_period
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, day, fail


def labelled_row_period(text):
    # OCR may omit the range's dash, but both complete dates and the label must
    # remain readable. Issue dates and credit-limit expiries are not coverage.
    matches = re.findall(rf"\bPERIODO\s*:?\s*({DATE})\s*(?:(?:A|ATE|[-–])\s*)?({DATE})\b",
                         normalize_upper_text(text))
    return unique_document_period(matches, parse_date=day) if matches else (None, None)


def contextual_row_date(value, period, fallback_year):
    if len(value) == 10:
        return day(value)
    start, end = period
    if start is None or end is None:
        if fallback_year is None:
            fail("Não há ano inequívoco para as datas abreviadas do extrato.")
        return day(f"{value}/{fallback_year}")
    candidates = set()
    for year in range(int(start[:4]), int(end[:4]) + 1):
        try:
            candidate = datetime.strptime(f"{value}/{year}", "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        if start <= candidate <= end:
            candidates.add(candidate)
    if len(candidates) != 1:
        fail("O período não confirma um ano único para a data do lançamento.")
    return candidates.pop()


def chronological_rows(rows, *, opening_position, first_row_position):
    ascending = all(a.transaction.date <= b.transaction.date for a, b in zip(rows, rows[1:]))
    descending = all(a.transaction.date >= b.transaction.date for a, b in zip(rows, rows[1:]))
    if not ascending and not descending:
        fail("A ordem de datas da tabela não pode ser confirmada.")
    # On a single date, the physical opening disambiguates the direction. Never
    # sort individual movements or pick a sequence that happens to reconcile.
    reverse = descending and (not ascending or opening_position is not None and opening_position > first_row_position)
    return list(reversed(rows)) if reverse else rows


def validate_idle_day_controls(rows, opening, controls):
    """A pending-only day still has a printed available-cash control to check."""
    cash_days = {row.transaction.date for row in rows}
    pending = iter(rows)
    current = next(pending, None)
    running, checked = opening, 0
    for date, values in sorted(controls.items()):
        if date in cash_days:
            continue
        while current is not None and current.transaction.date <= date:
            running += Decimal(str(current.transaction.amount))
            current = next(pending, None)
        if any(value != running for value in values):
            financial_mismatch()
        checked += len(values)
    return checked
