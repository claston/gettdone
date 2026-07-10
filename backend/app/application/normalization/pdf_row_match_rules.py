from __future__ import annotations

import re

from app.application.normalization.date import STATEMENT_DATE_TOKEN
from app.application.normalization.pdf_amount_tokens import is_amount_like

DATE_TOKEN = STATEMENT_DATE_TOKEN
INLINE_ROW_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
TABULAR_DATE_PREFIX_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
TABULAR_DATE_PREFIX_GLUE_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})(?P<rest>[A-Z0-9].+)$", re.IGNORECASE)
DATE_ONLY_PATTERN = re.compile(rf"^{DATE_TOKEN}$", re.IGNORECASE)
DATE_SEARCH_PATTERN = re.compile(rf"(?<!\d)(?P<date>{DATE_TOKEN})(?!\d)", re.IGNORECASE)
LEADING_NUMERIC_DATE_CANDIDATE_PATTERN = re.compile(
    r"^(?P<date>(?:\d{4}\s*-\s*\d{1,2}\s*-\s*\d{1,2}|"
    r"\d{1,2}\s*/\s*\d{1,4}(?:\s*/\s*\d{2,4})?|"
    r"\d{1,2}\s*-\s*\d{1,2}(?:\s*-\s*\d{2,4})?|"
    r"\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}))(?=\s|$)",
    re.IGNORECASE,
)


def match_inline_row(raw_line: str) -> re.Match[str] | None:
    return INLINE_ROW_PATTERN.match(raw_line)


def match_tabular_date_prefix(raw_line: str) -> re.Match[str] | None:
    match = TABULAR_DATE_PREFIX_PATTERN.match(raw_line)
    if match is not None:
        return match
    cleaned = raw_line.lstrip(" ([{_|=.:;-")
    spaced_match = TABULAR_DATE_PREFIX_PATTERN.match(cleaned)
    if spaced_match is not None:
        return spaced_match
    return TABULAR_DATE_PREFIX_GLUE_PATTERN.match(cleaned)


def is_date_only_row(raw_line: str) -> bool:
    return DATE_ONLY_PATTERN.fullmatch(raw_line) is not None


def is_amount_only_row(raw_line: str) -> bool:
    return is_amount_like(raw_line)


def contains_date_like(raw_text: str) -> bool:
    return DATE_SEARCH_PATTERN.search(raw_text) is not None


def extract_leading_date_candidate(raw_text: str) -> str | None:
    match = LEADING_NUMERIC_DATE_CANDIDATE_PATTERN.match(raw_text.strip())
    if match is None:
        return None
    return match.group("date")
