from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.normalization.date import MONTH_PATTERN, build_iso_date
from app.application.normalization.text import normalize_upper_text

DATE_HEADER_PATTERN = re.compile(rf"^(?P<day>\d{{2}})\s+(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})(?P<rest>.*)$")
MONTH_ONLY_DATE_PATTERN = re.compile(
    rf"^(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?(?P<rest>.*)$"
)
SLASH_DATE_PATTERN = re.compile(
    r"^(?P<day>\d{1,2})/(?P<month>\d{1,2})(?:/(?P<year>\d{2,4}))?(?P<rest>.*)$"
)
WEEKDAY_GROUP_DATE_PATTERN = re.compile(
    r"^(?:SEGUNDA|TERCA|QUARTA|QUINTA|SEXTA|SABADO|DOMINGO)(?:-FEIRA)?\s*,?\s*"
    r"(?P<day>\d{1,2})\s+DE\s+"
    r"(?P<month>JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)"
    r"\s+DE\s+(?P<year>\d{4})(?P<rest>.*)$"
)
FULL_MONTH_TO_ABBREV = {
    "JANEIRO": "JAN",
    "FEVEREIRO": "FEV",
    "MARCO": "MAR",
    "ABRIL": "ABR",
    "MAIO": "MAI",
    "JUNHO": "JUN",
    "JULHO": "JUL",
    "AGOSTO": "AGO",
    "SETEMBRO": "SET",
    "OUTUBRO": "OUT",
    "NOVEMBRO": "NOV",
    "DEZEMBRO": "DEZ",
}


@dataclass(frozen=True)
class GroupedDateMatch:
    date: str
    rest: str


def parse_grouped_date_line(raw_line: str, *, inferred_year: int | None) -> GroupedDateMatch | None:
    normalized_line = normalize_upper_text(raw_line)

    slash_match = SLASH_DATE_PATTERN.match(normalized_line)
    if slash_match:
        rest_value = (slash_match.group("rest") or "").strip()
        if rest_value:
            return None
        year_value = slash_match.group("year")
        if year_value is None:
            year_value = str(inferred_year if inferred_year is not None else datetime.now(timezone.utc).year)
        elif len(year_value) == 2:
            year_value = f"20{year_value}"
        return GroupedDateMatch(
            date=datetime(int(year_value), int(slash_match.group("month")), int(slash_match.group("day"))).strftime(
                "%Y-%m-%d"
            ),
            rest=rest_value,
        )

    weekday_match = WEEKDAY_GROUP_DATE_PATTERN.match(normalized_line)
    if weekday_match:
        month_abbrev = FULL_MONTH_TO_ABBREV.get(weekday_match.group("month"))
        if month_abbrev is None:
            return None
        return GroupedDateMatch(
            date=build_iso_date(
                year=weekday_match.group("year"),
                month_abbrev=month_abbrev,
                day=weekday_match.group("day"),
            ),
            rest=weekday_match.group("rest"),
        )

    date_match = DATE_HEADER_PATTERN.match(normalized_line)
    if date_match:
        return GroupedDateMatch(
            date=build_iso_date(
                year=date_match.group("year"),
                month_abbrev=date_match.group("month"),
                day=date_match.group("day"),
            ),
            rest=date_match.group("rest"),
        )

    month_only_match = MONTH_ONLY_DATE_PATTERN.match(normalized_line)
    if not month_only_match:
        return None

    year_value = month_only_match.group("year")
    if year_value is None:
        year_value = str(inferred_year if inferred_year is not None else datetime.now(timezone.utc).year)

    return GroupedDateMatch(
        date=build_iso_date(
            year=year_value,
            month_abbrev=month_only_match.group("month"),
            day=month_only_match.group("day"),
        ),
        rest=month_only_match.group("rest"),
    )
