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


def test_match_tabular_date_prefix_accepts_leading_ocr_symbol() -> None:
    match = match_tabular_date_prefix("(24/04/2024 TRANSFERENCIA 6.250,00C 6.428,50 C")
    assert match is not None
    assert match.group("date") == "24/04/2024"


def test_is_date_only_row_and_amount_only_row() -> None:
    assert is_date_only_row("2 out") is True
    assert is_date_only_row("2 out COMPRA") is False
    assert is_amount_only_row("R$ 10,00") is True
    assert is_amount_only_row("2.150,00 D") is True
    assert is_amount_only_row("212,05 C") is True
    assert is_amount_only_row("2.600,00 -") is True
    assert is_amount_only_row("R$ 10,00 saldo") is False
