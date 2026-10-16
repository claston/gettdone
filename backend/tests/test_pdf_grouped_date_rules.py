import pytest

from app.application.errors import InvalidFileContentError
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


def test_parse_grouped_date_line_keeps_slash_date_rest_opt_in() -> None:
    assert parse_grouped_date_line("01/04/2024 000123", inferred_year=2026) is None

    result = parse_grouped_date_line(
        "01/04/2024 000123",
        inferred_year=2026,
        allow_slash_date_rest=True,
    )

    assert result is not None
    assert result.date == "2024-04-01"
    assert result.rest == "000123"


def test_parse_grouped_date_line_supports_weekday_and_full_month_in_portuguese() -> None:
    result = parse_grouped_date_line("Quarta, 02 de fevereiro de 2022", inferred_year=2026)

    assert result is not None
    assert result.date == "2022-02-02"
    assert result.rest.strip() == ""


def test_parse_grouped_date_line_supports_leading_full_month_with_trailing_weekday() -> None:
    result = parse_grouped_date_line("17 de outubro de 2023, terça-feira", inferred_year=2026)

    assert result is not None
    assert result.date == "2023-10-17"
    assert result.rest.strip() == ""


@pytest.mark.parametrize("prefix", ["\uf166 ", " \ue001\t\uf166 ", "\U000f0001 ", "\U00100001 "])
@pytest.mark.parametrize("date_text", ["Quarta, 04 de março de 2026", "04/03/2026", "04 MAR 2026"])
def test_grouped_date_ignores_leading_private_use_font_icons(prefix: str, date_text: str) -> None:
    result = parse_grouped_date_line(prefix + date_text, inferred_year=None)

    assert result is not None
    assert result.date == "2026-03-04"
    assert result.rest.strip() == ""


@pytest.mark.parametrize("text", [
    "Emitido em \uf166 Quarta, 04 de março de 2026",
    "\uf166 Referência 04/03/2026",
    "04 \uf166 MAR 2026",
    "\uf166 04/03/2026 000123",
    "\uf166",
])
def test_grouped_date_does_not_remove_text_or_internal_icons(text: str) -> None:
    assert parse_grouped_date_line(text, inferred_year=2026) is None


def test_grouped_date_with_icon_still_rejects_impossible_date() -> None:
    with pytest.raises(InvalidFileContentError):
        parse_grouped_date_line("\uf166 Segunda, 31 de fevereiro de 2026", inferred_year=None)
