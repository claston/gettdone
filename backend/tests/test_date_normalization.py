import re
from datetime import datetime, timezone

import pytest

from app.application.errors import InvalidFileContentError
from app.application.normalization.date import build_iso_date, infer_default_statement_year, parse_statement_date


def test_parse_statement_date_supports_slash_full_year() -> None:
    assert parse_statement_date("01/04/2026", fallback_year=None) == "2026-04-01"


def test_parse_statement_date_supports_slash_short_year() -> None:
    assert parse_statement_date("01/04/26", fallback_year=None) == "2026-04-01"


def test_parse_statement_date_supports_day_month_without_year_with_fallback() -> None:
    assert parse_statement_date("1/4", fallback_year=2025) == "2025-04-01"


def test_parse_statement_date_supports_month_abbrev_with_fallback() -> None:
    assert parse_statement_date("1 ABR", fallback_year=2024) == "2024-04-01"


def test_parse_statement_date_uses_current_year_when_fallback_missing() -> None:
    expected_year = datetime.now(timezone.utc).year
    parsed = parse_statement_date("1/4", fallback_year=None)
    assert re.fullmatch(rf"{expected_year}-04-01", parsed)


def test_parse_statement_date_rejects_invalid_input() -> None:
    with pytest.raises(InvalidFileContentError, match="Invalid date value"):
        parse_statement_date("abc", fallback_year=None)


def test_build_iso_date_rejects_invalid_month() -> None:
    with pytest.raises(InvalidFileContentError, match="Invalid month value"):
        build_iso_date("2026", "XXX", "01")


def test_infer_default_statement_year_prefers_most_frequent_year() -> None:
    lines = [
        "01/04/2025 PIX RECEBIDO 100,00",
        "15/04/2025 TARIFA 20,00",
        "20 ABR 2024 AJUSTE",
    ]

    assert infer_default_statement_year(lines) == 2025


def test_infer_default_statement_year_returns_none_when_absent() -> None:
    assert infer_default_statement_year(["SEM DATA", "PIX RECEBIDO"]) is None
