from app.application.normalization.pdf_grouped_date_rules import parse_grouped_date_line


def test_parse_grouped_date_line_with_explicit_year() -> None:
    result = parse_grouped_date_line("16 MAR 2026 Compra mercado R$ 10,00", inferred_year=2025)

    assert result is not None
    assert result.date == "2026-03-16"
    assert result.rest.strip() == "COMPRA MERCADO R$ 10,00"


def test_parse_grouped_date_line_uses_inferred_year_for_month_only() -> None:
    result = parse_grouped_date_line("7 ABR Pix recebido", inferred_year=2024)

    assert result is not None
    assert result.date == "2024-04-07"
    assert result.rest.strip() == "PIX RECEBIDO"


def test_parse_grouped_date_line_returns_none_for_non_date_lines() -> None:
    assert parse_grouped_date_line("SALDO ANTERIOR", inferred_year=2024) is None


def test_parse_grouped_date_line_supports_slash_date_prefix() -> None:
    result = parse_grouped_date_line("01/04/2024", inferred_year=2026)

    assert result is not None
    assert result.date == "2024-04-01"
    assert result.rest.strip() == ""
