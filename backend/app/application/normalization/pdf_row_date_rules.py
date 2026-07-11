import re

from app.application.normalization.date import parse_statement_date


def parse_row_date(
    raw_date: str,
    *,
    fallback_year: int | None,
    date_formats: tuple[str, ...] = (),
) -> str:
    normalized_date = _normalize_profile_date(raw_date, date_formats=date_formats)
    return parse_statement_date(normalized_date, fallback_year=fallback_year)


def _normalize_profile_date(raw_date: str, *, date_formats: tuple[str, ...]) -> str:
    value = raw_date.strip()
    if any("HH:mm" in item for item in date_formats):
        value = re.sub(r"\s+\d{1,2}\s*:\s*\d{2}(?::\d{2})?$", "", value).strip()
    if "ddMMyyyy" in date_formats and re.fullmatch(r"\d{8}", value):
        return f"{value[:2]}/{value[2:4]}/{value[4:]}"
    if "ddMMyy" in date_formats and re.fullmatch(r"\d{6}", value):
        return f"{value[:2]}/{value[2:4]}/{value[4:]}"
    if "ddMMM" in date_formats and (compact_month_match := re.fullmatch(r"(\d{1,2})([A-Za-z]{3})", value)):
        return f"{compact_month_match.group(1)}/{compact_month_match.group(2)}"
    return value
