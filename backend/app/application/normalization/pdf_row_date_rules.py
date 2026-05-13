from app.application.normalization.date import parse_statement_date


def parse_row_date(raw_date: str, *, fallback_year: int | None) -> str:
    return parse_statement_date(raw_date, fallback_year=fallback_year)
