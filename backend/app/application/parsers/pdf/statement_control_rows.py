"""Account controls are evidence, never imported financial movements."""
import re

from app.application.normalization.text import normalize_upper_text

_OPENING = re.compile(r"^(?:(?:ATE\s+)?\d{2}/\d{2}/\d{4}\s+)?SALDO\s+(?:ANTERIOR|INICIAL)\b")


def is_opening_control(row):
    transaction = getattr(row, "transaction", row)
    return bool(_OPENING.match(normalize_upper_text(transaction.description)))


def movement_candidates(result):
    rows, candidates = result
    # Keep existing parser precedence when a candidate contains movements.
    # Opening-only candidates must not displace a reader of actual cash rows.
    return (rows if any(not is_opening_control(row) for row in rows) else []), candidates
