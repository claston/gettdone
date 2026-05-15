from app.application.normalization.pdf_amount_tokens import (
    find_amount_tokens,
    has_explicit_amount_sign,
    is_amount_like,
    parse_pdf_amount,
)


def test_find_amount_tokens_extracts_positions_and_values() -> None:
    text = "PIX RECEBIDO 1.000,00 TARIFA 12,34"
    tokens = find_amount_tokens(text)

    assert len(tokens) == 2
    assert tokens[0].value == "1.000,00"
    assert tokens[1].value == "12,34"
    assert text[tokens[0].start : tokens[0].end] == "1.000,00"
    assert text[tokens[1].start : tokens[1].end] == "12,34"


def test_find_amount_tokens_does_not_consume_next_balance_negative_sign() -> None:
    text = "TARIFA 310301 -7,00 -448,66"
    tokens = find_amount_tokens(text)

    assert len(tokens) == 2
    assert tokens[0].value == "-7,00"
    assert tokens[1].value == "-448,66"


def test_parse_pdf_amount_handles_currency_and_unicode_minus() -> None:
    assert parse_pdf_amount("R$ 1.234,56") == 1234.56
    assert parse_pdf_amount("-10,00") == -10.0


def test_is_amount_like_accepts_supported_shapes() -> None:
    assert is_amount_like("R$ 1.234,56")
    assert is_amount_like("-12,34")
    assert is_amount_like("−12,34")
    assert not is_amount_like("VALOR INDEFINIDO")


def test_parse_pdf_amount_supports_credit_debit_suffix() -> None:
    assert parse_pdf_amount("2.150,00 D") == -2150.0
    assert parse_pdf_amount("212,05 C") == 212.05
    assert parse_pdf_amount("2.600,00 -") == -2600.0


def test_has_explicit_amount_sign_detects_supported_sign_forms() -> None:
    assert has_explicit_amount_sign("-10,00")
    assert has_explicit_amount_sign("(10,00)")
    assert has_explicit_amount_sign("10,00 D")
    assert has_explicit_amount_sign("10,00 C")
    assert not has_explicit_amount_sign("10,00")
