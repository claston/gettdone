import re
from datetime import datetime, timezone

from app.application.errors import InvalidFileContentError
from app.application.normalization.text import normalize_upper_text

MONTH_TO_NUMBER = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}
MONTH_PATTERN = "|".join(MONTH_TO_NUMBER)


def parse_statement_date(raw: str, fallback_year: int | None) -> str:
    value = raw.strip()
    upper_value = normalize_upper_text(value)
    compact_value = re.sub(r"\s*/\s*", "/", value)
    compact_upper_value = re.sub(r"\s*/\s*", "/", upper_value)
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", compact_value):
        return _parse_slash_date(compact_value)

    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", compact_value):
        day, month, year = compact_value.split("/")
        return _parse_slash_date(f"{day}/{month}/20{year}")

    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{3}", compact_value):
        day, month, year_prefix = compact_value.split("/")
        resolved_year = _resolve_truncated_year_prefix(year_prefix, fallback_year=fallback_year)
        return _parse_slash_date(f"{day}/{month}/{resolved_year}")

    if re.fullmatch(r"\d{1,2}/\d{1,2}", compact_value):
        if fallback_year is None:
            fallback_year = datetime.now(timezone.utc).year
        return _parse_slash_date(f"{compact_value}/{fallback_year}")

    month_slash_match = re.fullmatch(rf"(?P<day>\d{{1,2}})/(?P<month>{MONTH_PATTERN})(?:/(?P<year>\d{{3,4}}))?", compact_upper_value)
    if month_slash_match:
        year_raw = month_slash_match.group("year")
        if year_raw:
            if len(year_raw) == 3:
                year_value = _resolve_truncated_year_prefix(year_raw, fallback_year=fallback_year)
            else:
                year_value = int(year_raw)
        else:
            year_value = fallback_year if fallback_year is not None else datetime.now(timezone.utc).year
        return build_iso_date(year=str(year_value), month_abbrev=month_slash_match.group("month"), day=month_slash_match.group("day"))

    month_match = re.fullmatch(rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{3,4}}))?", upper_value)
    if month_match:
        day = int(month_match.group("day"))
        month_abbrev = month_match.group("month")
        month_value = MONTH_TO_NUMBER.get(month_abbrev)
        if month_value is None:
            raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
        year_raw = month_match.group("year")
        if year_raw:
            if len(year_raw) == 3:
                year_value = _resolve_truncated_year_prefix(year_raw, fallback_year=fallback_year)
            else:
                year_value = int(year_raw)
        else:
            year_value = fallback_year if fallback_year is not None else datetime.now(timezone.utc).year
        try:
            return datetime(year_value, month_value, day).strftime("%Y-%m-%d")
        except ValueError as exc:
            raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc

    raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.")


def build_iso_date(year: str, month_abbrev: str, day: str) -> str:
    month_value = MONTH_TO_NUMBER.get(month_abbrev)
    if month_value is None:
        raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
    try:
        return datetime(int(year), month_value, int(day)).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {day}/{month_abbrev}/{year}.") from exc


def infer_default_statement_year(lines: list[str]) -> int | None:
    year_counts: dict[int, int] = {}

    for line in lines:
        for raw in re.findall(r"\b\d{2}/\d{2}/(\d{4})\b", line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(r"\b\d{2}/\d{2}/(\d{2})\b", line):
            year = int(f"20{raw}")
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        normalized_line = normalize_upper_text(line)
        for raw in re.findall(rf"\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+(\d{{4}})\b", normalized_line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1

    if not year_counts:
        return None
    return max(year_counts.items(), key=lambda item: item[1])[0]


def _parse_slash_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc


def _resolve_truncated_year_prefix(year_prefix: str, *, fallback_year: int | None) -> int:
    if fallback_year is not None and str(fallback_year).startswith(year_prefix):
        return fallback_year
    current_year = datetime.now(timezone.utc).year
    if str(current_year).startswith(year_prefix):
        return current_year
    raise InvalidFileContentError(f"Invalid date value in PDF statement: {year_prefix!r}.")
