from app.application.normalization.pdf_row_date_rules import parse_row_date


def test_parse_row_date_with_explicit_year() -> None:
    assert parse_row_date("02/10/2024", fallback_year=2026) == "2024-10-02"


def test_parse_row_date_with_fallback_year() -> None:
    assert parse_row_date("2 out", fallback_year=2024) == "2024-10-02"


def test_parse_row_date_accepts_ocr_truncated_slash_year_with_fallback() -> None:
    assert parse_row_date("17/01/202", fallback_year=2024) == "2024-01-17"


def test_parse_row_date_accepts_ocr_truncated_month_year_with_fallback() -> None:
    assert parse_row_date("17 JAN 202", fallback_year=2024) == "2024-01-17"
