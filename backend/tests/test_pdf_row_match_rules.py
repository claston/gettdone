from app.application.normalization.pdf_row_match_rules import (
    is_amount_only_row,
    is_date_only_row,
    match_inline_row,
    match_tabular_date_prefix,
)


def test_match_inline_row_returns_date_and_rest() -> None:
    match = match_inline_row("02/10/2024 PIX RECEBIDO 10,00")
    assert match is not None
    assert match.group("date") == "02/10/2024"
    assert match.group("rest") == "PIX RECEBIDO 10,00"


def test_match_tabular_date_prefix_returns_date_and_rest() -> None:
    match = match_tabular_date_prefix("2 out COMPRA 10,00 100,00")
    assert match is not None
    assert match.group("date") == "2 out"


def test_match_tabular_date_prefix_accepts_spaced_month_slash_format() -> None:
    match = match_tabular_date_prefix("01 / jul TRANSF TITUL TED 4015 -109,50 -99,50")
    assert match is not None
    assert match.group("date") == "01 / jul"
    assert match.group("rest").startswith("TRANSF TITUL TED")


def test_match_tabular_date_prefix_accepts_common_numeric_date_separators() -> None:
    expected_dates = {
        "01-07-2026 TRANSFERENCIA 10,00": "01-07-2026",
        "02.07.2026 PIX RECEBIDO 20,00": "02.07.2026",
        "2026-07-03 TARIFA 5,00": "2026-07-03",
    }

    for raw_line, expected_date in expected_dates.items():
        match = match_tabular_date_prefix(raw_line)
        assert match is not None
        assert match.group("date") == expected_date


def test_match_tabular_date_prefix_accepts_profile_compact_date_format() -> None:
    match = match_tabular_date_prefix(
        "10042021 LIQUIDO COBRANCA 100,00",
        date_formats=("ddMMyyyy",),
    )

    assert match is not None
    assert match.group("date") == "10042021"
    assert match.group("rest") == "LIQUIDO COBRANCA 100,00"


def test_match_tabular_date_prefix_consumes_profile_timestamp() -> None:
    match = match_tabular_date_prefix(
        "10/04/2021 14:35 PIX RECEBIDO 100,00",
        date_formats=("dd/MM/yyyy HH:mm",),
    )

    assert match is not None
    assert match.group("date") == "10/04/2021 14:35"
    assert match.group("rest") == "PIX RECEBIDO 100,00"


def test_match_inline_row_accepts_full_portuguese_month() -> None:
    match = match_inline_row("4 de julho de 2026 PIX RECEBIDO 10,00")

    assert match is not None
    assert match.group("date") == "4 de julho de 2026"
    assert match.group("rest") == "PIX RECEBIDO 10,00"


def test_match_tabular_date_prefix_accepts_leading_ocr_symbol() -> None:
    match = match_tabular_date_prefix("(24/04/2024 TRANSFERENCIA 6.250,00C 6.428,50 C")
    assert match is not None
    assert match.group("date") == "24/04/2024"


def test_match_tabular_date_prefix_accepts_ocr_glued_date_and_description() -> None:
    match = match_tabular_date_prefix("03/03/2024TARIFA PACOTE SERVICOS030324886 -32,40 5.908,12")
    assert match is not None
    assert match.group("date") == "03/03/2024"
    assert match.group("rest").startswith("TARIFA PACOTE SERVICOS")


def test_match_tabular_date_prefix_accepts_leading_ocr_noise_prefix() -> None:
    match = match_tabular_date_prefix("_ 07/01/2024 TARIFA BANCARIA = 0701963 . == -82,24 -5.625,47 -")
    assert match is not None
    assert match.group("date") == "07/01/2024"
    assert "0701963" in match.group("rest")


def test_is_date_only_row_and_amount_only_row() -> None:
    assert is_date_only_row("2 out") is True
    assert is_date_only_row("01-07-2026") is True
    assert is_date_only_row("2026-07-01") is True
    assert is_date_only_row("2 out COMPRA") is False
    assert is_amount_only_row("R$ 10,00") is True
    assert is_amount_only_row("2.150,00 D") is True
    assert is_amount_only_row("212,05 C") is True
    assert is_amount_only_row("2.600,00 -") is True
    assert is_amount_only_row("R$ 10,00 saldo") is False
