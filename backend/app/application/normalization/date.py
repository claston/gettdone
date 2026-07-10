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
FULL_MONTH_TO_NUMBER = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}
FULL_MONTH_PATTERN = (
    r"JANEIRO|FEVEREIRO|MAR(?:C|Ç)O|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO"
)
DATE_DAY_TOKEN = r"(?:0?[1-9]|[12]\d|3[01])"
DATE_NUMERIC_MONTH_TOKEN = r"(?:0?[1-9]|1[0-2])"
DATE_ISO_TOKEN = rf"\d{{4}}\s*-\s*{DATE_NUMERIC_MONTH_TOKEN}\s*-\s*{DATE_DAY_TOKEN}"
DATE_NUMERIC_TOKEN = (
    rf"(?:{DATE_DAY_TOKEN}\s*/\s*{DATE_NUMERIC_MONTH_TOKEN}(?:\s*/\s*\d{{2,4}})?|"
    rf"{DATE_DAY_TOKEN}\s*-\s*{DATE_NUMERIC_MONTH_TOKEN}(?:\s*-\s*\d{{2,4}})?|"
    rf"{DATE_DAY_TOKEN}\s*\.\s*{DATE_NUMERIC_MONTH_TOKEN}\s*\.\s*\d{{2,4}})"
)
DATE_ABBREVIATED_MONTH_TOKEN = (
    rf"{DATE_DAY_TOKEN}\s*[./-]\s*(?:{MONTH_PATTERN})(?:\s*[./-]\s*\d{{2,4}})?"
)
DATE_SPACED_MONTH_TOKEN = rf"{DATE_DAY_TOKEN}\s+(?:{MONTH_PATTERN})(?:\s+\d{{2,4}})?"
DATE_FULL_MONTH_TOKEN = rf"{DATE_DAY_TOKEN}\s+DE\s+(?:{FULL_MONTH_PATTERN})(?:\s+DE\s+\d{{2,4}})?"
STATEMENT_DATE_TOKEN = (
    rf"(?:{DATE_ISO_TOKEN}|{DATE_NUMERIC_TOKEN}|{DATE_ABBREVIATED_MONTH_TOKEN}|"
    rf"{DATE_FULL_MONTH_TOKEN}|{DATE_SPACED_MONTH_TOKEN})"
)


def parse_statement_date(raw: str, fallback_year: int | None) -> str:
    value = raw.strip()
    upper_value = normalize_upper_text(value)
    compact_value = re.sub(r"\s*([./-])\s*", r"\1", value)
    compact_upper_value = re.sub(r"\s*([./-])\s*", r"\1", upper_value)

    iso_match = re.fullmatch(
        r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})",
        compact_value,
    )
    if iso_match:
        return _build_numeric_iso_date(
            year=int(iso_match.group("year")),
            month=int(iso_match.group("month")),
            day=int(iso_match.group("day")),
            raw=raw,
        )

    numeric_match = re.fullmatch(
        r"(?P<day>\d{1,2})(?P<separator>[./-])(?P<month>\d{1,2})"
        r"(?:(?P=separator)(?P<year>\d{2,4}))?",
        compact_value,
    )
    if numeric_match:
        if numeric_match.group("separator") == "." and numeric_match.group("year") is None:
            raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.")
        return _build_numeric_iso_date(
            year=_resolve_statement_year(numeric_match.group("year"), fallback_year=fallback_year),
            month=int(numeric_match.group("month")),
            day=int(numeric_match.group("day")),
            raw=raw,
        )

    month_slash_match = re.fullmatch(
        rf"(?P<day>\d{{1,2}})(?P<separator>[./-])(?P<month>{MONTH_PATTERN})"
        r"(?:(?P=separator)(?P<year>\d{2,4}))?",
        compact_upper_value,
    )
    if month_slash_match:
        return build_iso_date(
            year=str(_resolve_statement_year(month_slash_match.group("year"), fallback_year=fallback_year)),
            month_abbrev=month_slash_match.group("month"),
            day=month_slash_match.group("day"),
        )

    full_month_match = re.fullmatch(
        rf"(?P<day>\d{{1,2}})\s+DE\s+(?P<month>{'|'.join(FULL_MONTH_TO_NUMBER)})"
        r"(?:\s+DE\s+(?P<year>\d{2,4}))?",
        upper_value,
    )
    if full_month_match:
        month_value = FULL_MONTH_TO_NUMBER.get(full_month_match.group("month"))
        if month_value is None:
            raise InvalidFileContentError(f"Invalid month value in PDF statement: {raw!r}.")
        return _build_numeric_iso_date(
            year=_resolve_statement_year(full_month_match.group("year"), fallback_year=fallback_year),
            month=month_value,
            day=int(full_month_match.group("day")),
            raw=raw,
        )

    month_match = re.fullmatch(
        rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{2,4}}))?",
        upper_value,
    )
    if month_match:
        day = int(month_match.group("day"))
        month_abbrev = month_match.group("month")
        month_value = MONTH_TO_NUMBER.get(month_abbrev)
        if month_value is None:
            raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
        return _build_numeric_iso_date(
            year=_resolve_statement_year(month_match.group("year"), fallback_year=fallback_year),
            month=month_value,
            day=day,
            raw=raw,
        )

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
        normalized_line = normalize_upper_text(line)
        for raw in re.findall(r"\b(?:MES\s+REFERENCIA|PERIODO)\s*:\s*\d{1,2}/(\d{4})\b", normalized_line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-](\d{4})\b", line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-](\d{2})\b", line):
            year = int(f"20{raw}")
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(rf"\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+(\d{{4}})\b", normalized_line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(r"\b(\d{4})-\d{1,2}-\d{1,2}\b", line):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1
        for raw in re.findall(
            rf"\b\d{{1,2}}\s+DE\s+(?:{'|'.join(FULL_MONTH_TO_NUMBER)})\s+DE\s+(\d{{4}})\b",
            normalized_line,
        ):
            year = int(raw)
            if not 1900 <= year <= 2100:
                continue
            year_counts[year] = year_counts.get(year, 0) + 1

    if not year_counts:
        return None
    return max(year_counts.items(), key=lambda item: item[1])[0]


def _resolve_statement_year(year_raw: str | None, *, fallback_year: int | None) -> int:
    if year_raw is None:
        return fallback_year if fallback_year is not None else datetime.now(timezone.utc).year
    if len(year_raw) == 2:
        return int(f"20{year_raw}")
    if len(year_raw) == 3:
        return _resolve_truncated_year_prefix(year_raw, fallback_year=fallback_year)
    return int(year_raw)


def _build_numeric_iso_date(*, year: int, month: int, day: int, raw: str) -> str:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc


def _resolve_truncated_year_prefix(year_prefix: str, *, fallback_year: int | None) -> int:
    if fallback_year is not None and str(fallback_year).startswith(year_prefix):
        return fallback_year
    current_year = datetime.now(timezone.utc).year
    if str(current_year).startswith(year_prefix):
        return current_year
    raise InvalidFileContentError(f"Invalid date value in PDF statement: {year_prefix!r}.")
