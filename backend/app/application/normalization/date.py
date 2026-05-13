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
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        return _parse_slash_date(value)

    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", value):
        day, month, year = value.split("/")
        return _parse_slash_date(f"{day}/{month}/20{year}")

    if re.fullmatch(r"\d{1,2}/\d{1,2}", value):
        if fallback_year is None:
            fallback_year = datetime.now(timezone.utc).year
        return _parse_slash_date(f"{value}/{fallback_year}")

    month_match = re.fullmatch(rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?", upper_value)
    if month_match:
        day = int(month_match.group("day"))
        month_abbrev = month_match.group("month")
        month_value = MONTH_TO_NUMBER.get(month_abbrev)
        if month_value is None:
            raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
        year_raw = month_match.group("year")
        if year_raw:
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
            year_counts[year] = year_counts.get(year, 0) + 1
        normalized_line = normalize_upper_text(line)
        for raw in re.findall(rf"\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+(\d{{4}})\b", normalized_line):
            year = int(raw)
            year_counts[year] = year_counts.get(year, 0) + 1

    if not year_counts:
        return None
    return max(year_counts.items(), key=lambda item: item[1])[0]


def _parse_slash_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc
