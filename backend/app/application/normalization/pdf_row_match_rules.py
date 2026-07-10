from __future__ import annotations

import re
from functools import lru_cache

from app.application.normalization.date import MONTH_PATTERN, STATEMENT_DATE_TOKEN
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
_PROFILE_DATE_FORMAT_TOKEN_PATTERN = re.compile(r"yyyy|yy|MMM|MM|dd|HH|mm")
_PROFILE_DATE_FORMAT_TOKENS = {
    "yyyy": r"\d{4}",
    "yy": r"\d{2}",
    "MMM": rf"(?:{MONTH_PATTERN})",
    "MM": r"(?:0?[1-9]|1[0-2])",
    "dd": r"(?:0?[1-9]|[12]\d|3[01])",
    "HH": r"(?:[01]?\d|2[0-3])",
    "mm": r"[0-5]\d",
}


def match_inline_row(raw_line: str) -> re.Match[str] | None:
    return INLINE_ROW_PATTERN.match(raw_line)


def match_tabular_date_prefix(
    raw_line: str,
    *,
    date_formats: tuple[str, ...] = (),
) -> re.Match[str] | None:
    profile_pattern = _profile_date_prefix_pattern(date_formats)
    if profile_pattern is not None:
        profile_match = profile_pattern.match(raw_line)
        if profile_match is not None:
            return profile_match
    match = TABULAR_DATE_PREFIX_PATTERN.match(raw_line)
    if match is not None:
        return match
    cleaned = raw_line.lstrip(" ([{_|=.:;-")
    spaced_match = TABULAR_DATE_PREFIX_PATTERN.match(cleaned)
    if spaced_match is not None:
        return spaced_match
    return TABULAR_DATE_PREFIX_GLUE_PATTERN.match(cleaned)


def is_date_only_row(raw_line: str, *, date_formats: tuple[str, ...] = ()) -> bool:
    profile_pattern = _profile_date_only_pattern(date_formats)
    if profile_pattern is not None and profile_pattern.fullmatch(raw_line) is not None:
        return True
    return DATE_ONLY_PATTERN.fullmatch(raw_line) is not None


def is_amount_only_row(raw_line: str) -> bool:
    return is_amount_like(raw_line)


def contains_date_like(raw_text: str, *, date_formats: tuple[str, ...] = ()) -> bool:
    if DATE_SEARCH_PATTERN.search(raw_text) is not None:
        return True
    profile_pattern = _profile_date_search_pattern(date_formats)
    return profile_pattern is not None and profile_pattern.search(raw_text) is not None


def extract_leading_date_candidate(raw_text: str) -> str | None:
    match = LEADING_NUMERIC_DATE_CANDIDATE_PATTERN.match(raw_text.strip())
    if match is None:
        return None
    return match.group("date")


@lru_cache(maxsize=64)
def _profile_date_prefix_pattern(date_formats: tuple[str, ...]) -> re.Pattern[str] | None:
    date_token = _compile_profile_date_token(date_formats)
    if not date_token:
        return None
    return re.compile(rf"^(?P<date>{date_token})\s+(?P<rest>.+)$", re.IGNORECASE)


@lru_cache(maxsize=64)
def _profile_date_only_pattern(date_formats: tuple[str, ...]) -> re.Pattern[str] | None:
    date_token = _compile_profile_date_token(date_formats)
    if not date_token:
        return None
    return re.compile(rf"^(?:{date_token})$", re.IGNORECASE)


@lru_cache(maxsize=64)
def _profile_date_search_pattern(date_formats: tuple[str, ...]) -> re.Pattern[str] | None:
    date_token = _compile_profile_date_token(date_formats)
    if not date_token:
        return None
    return re.compile(rf"(?<!\d)(?:{date_token})(?!\d)", re.IGNORECASE)


def _compile_profile_date_token(date_formats: tuple[str, ...]) -> str:
    compiled_formats = [compiled for raw in date_formats if (compiled := _compile_profile_date_format(raw))]
    if not compiled_formats:
        return ""
    return "(?:" + "|".join(compiled_formats) + ")"


def _compile_profile_date_format(raw_format: str) -> str:
    value = raw_format.strip()
    if not value:
        return ""
    parts: list[str] = []
    cursor = 0
    for match in _PROFILE_DATE_FORMAT_TOKEN_PATTERN.finditer(value):
        parts.append(_compile_profile_date_literal(value[cursor : match.start()]))
        parts.append(_PROFILE_DATE_FORMAT_TOKENS[match.group(0)])
        cursor = match.end()
    if cursor == 0:
        return ""
    parts.append(_compile_profile_date_literal(value[cursor:]))
    return "".join(parts)


def _compile_profile_date_literal(value: str) -> str:
    parts: list[str] = []
    for char in value:
        if char.isspace():
            if not parts or parts[-1] != r"\s+":
                parts.append(r"\s+")
        elif char in "/.-:":
            parts.append(rf"\s*{re.escape(char)}\s*")
        else:
            parts.append(re.escape(char))
    return "".join(parts)
