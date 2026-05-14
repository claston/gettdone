from __future__ import annotations

import re

from app.application.normalization.date import MONTH_PATTERN

SIGN_TOKEN = r"[+\-\u2212]"
AMOUNT_PATTERN = re.compile(
    rf"^(?:{SIGN_TOKEN}\s*)?(?:R\$\s*)?\d+(?:\.\d{{3}})*,\d{{2}}(?:\s*{SIGN_TOKEN})?(?:\s*[CD])?$"
)

DATE_SLASH_TOKEN = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
DATE_MONTH_TOKEN = rf"\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?"
DATE_TOKEN = rf"(?:{DATE_SLASH_TOKEN}|{DATE_MONTH_TOKEN})"
INLINE_ROW_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
TABULAR_DATE_PREFIX_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
DATE_ONLY_PATTERN = re.compile(rf"^{DATE_TOKEN}$", re.IGNORECASE)


def match_inline_row(raw_line: str) -> re.Match[str] | None:
    return INLINE_ROW_PATTERN.match(raw_line)


def match_tabular_date_prefix(raw_line: str) -> re.Match[str] | None:
    return TABULAR_DATE_PREFIX_PATTERN.match(raw_line)


def is_date_only_row(raw_line: str) -> bool:
    return DATE_ONLY_PATTERN.fullmatch(raw_line) is not None


def is_amount_only_row(raw_line: str) -> bool:
    return AMOUNT_PATTERN.fullmatch(raw_line) is not None
