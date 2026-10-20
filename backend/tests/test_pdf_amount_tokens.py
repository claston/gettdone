import pytest

from app.application.normalization.pdf_amount_tokens import (
    find_amount_tokens,
    find_profile_amount_tokens,
    has_amount_token_explicit_sign,
    has_explicit_amount_sign,
    is_amount_like,
    parse_amount_token,
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


def test_find_amount_tokens_keeps_immediate_trailing_minus_with_amount_before_balance() -> None:
    text = "PIX ENVIADO 1.500,00- 4.629,72"
    tokens = find_amount_tokens(text)

    assert len(tokens) == 2
    assert tokens[0].value == "1.500,00-"
    assert tokens[1].value == "4.629,72"


def test_find_amount_tokens_preserves_currency_prefixed_negative_balance() -> None:
    text = "PIX ENVIADO R$ 4.308,00 -R$ 3.297,46"

    tokens = find_amount_tokens(text)

    assert [token.value for token in tokens] == ["R$ 4.308,00", "-R$ 3.297,46"]
    assert [parse_pdf_amount(token.value) for token in tokens] == [4308.0, -3297.46]


def test_find_amount_tokens_does_not_consume_counterparty_initial_as_debit_suffix() -> None:
    text = "SAIDA - R$ 1.646,68 R$ 5.047,38 DAS SIMPLES NACIONAL"

    tokens = find_amount_tokens(text)

    assert [token.value for token in tokens] == ["- R$ 1.646,68", "R$ 5.047,38"]
    assert [parse_pdf_amount(token.value) for token in tokens] == [-1646.68, 5047.38]


def test_find_amount_tokens_keeps_international_and_spaced_thousands_complete() -> None:
    text = "PIX RECEBIDO 1,234.56 SALDO 2 345,67"

    tokens = find_amount_tokens(text)

    assert [token.value for token in tokens] == ["1,234.56", "2 345,67"]
    assert [parse_pdf_amount(token.value) for token in tokens] == [1234.56, 2345.67]


def test_find_amount_tokens_rejects_partial_numeric_matches() -> None:
    assert find_amount_tokens("VALOR OCR 1.234,5G") == []
    assert find_amount_tokens("VALOR COM TRES DECIMAIS 1234.567") == []


def test_parse_pdf_amount_handles_currency_and_unicode_minus() -> None:
    assert parse_pdf_amount("R$ 1.234,56") == 1234.56
    assert parse_pdf_amount("US$ 1,234.56") == 1234.56
    assert parse_pdf_amount("-10,00") == -10.0


def test_is_amount_like_accepts_supported_shapes() -> None:
    assert is_amount_like("R$ 1.234,56")
    assert is_amount_like("US$ 1,234.56")
    assert is_amount_like("2 345,67")
    assert is_amount_like("-12,34")
    assert is_amount_like("−12,34")
    assert not is_amount_like("1.234,5G")
    assert not is_amount_like("VALOR INDEFINIDO")


def test_parse_pdf_amount_supports_credit_debit_suffix() -> None:
    assert parse_pdf_amount("2.150,00 D") == -2150.0
    assert parse_pdf_amount("212,05 C") == 212.05
    assert parse_pdf_amount("2.600,00 -") == -2600.0


def test_has_explicit_amount_sign_detects_supported_sign_forms() -> None:
    assert has_explicit_amount_sign("-10,00")
    assert has_explicit_amount_sign("(10,00)")
    assert has_explicit_amount_sign("10,00D")
    assert has_explicit_amount_sign("10,00C")
    assert has_explicit_amount_sign("10,00 D")
    assert has_explicit_amount_sign("10,00 C")
    assert not has_explicit_amount_sign("10,00")


def test_find_profile_amount_tokens_applies_declared_debit_suffix() -> None:
    tokens = find_profile_amount_tokens(
        "PAGAMENTO 125,40 DB",
        positive_patterns=("{amount} CR",),
        negative_patterns=("{amount} DB",),
    )

    assert len(tokens) == 1
    assert tokens[0].value == "125,40"
    assert tokens[0].role_hint == "debit"
    assert parse_amount_token(tokens[0]) == -125.4
    assert has_amount_token_explicit_sign(tokens[0]) is True


def test_find_profile_amount_tokens_applies_declared_credit_suffix() -> None:
    tokens = find_profile_amount_tokens(
        "RECEBIMENTO 125,40 CR",
        positive_patterns=("{amount} CR",),
        negative_patterns=("{amount} DB",),
    )

    assert len(tokens) == 1
    assert tokens[0].role_hint == "credit"
    assert parse_amount_token(tokens[0]) == 125.4


@pytest.mark.parametrize("reference", [
    "ABCDEF12-1234-4ABC-9876-ABCDEF0000B4",
    "ABCDEF12-1234-4ABC-9876-ABCDEF0000A9",
    "080----85", "DOC/85", "DOC_85", "REF85",
])
@pytest.mark.parametrize("declared", [False, True])
def test_identifier_suffix_is_not_part_of_a_spaced_monetary_amount(reference, declared):
    text = f"CREDITO CLIENTE {reference} 134,46 1.134,46"
    tokens = (find_profile_amount_tokens(text, negative_patterns=("-{amount}",), positive_patterns=("+{amount}",))
              if declared else find_amount_tokens(text))
    assert [parse_amount_token(token) for token in tokens] == [134.46, 1134.46]
    assert text[:tokens[0].start].strip().endswith(reference)
    assert all(text[token.start:token.end] == token.value for token in tokens)


@pytest.mark.parametrize("value,expected", [
    ("-1 234,56", -1234.56), ("+1 234,56", 1234.56), ("−1 234,56", -1234.56),
    ("R$ 1 234,56", 1234.56), ("-R$ 1 234,56", -1234.56), ("R$ -1 234,56", -1234.56),
    ("US$ 1,234.56", 1234.56), ("1\u00a0234,56 D", -1234.56),
    ("(1 234,56)", -1234.56), ("1 234,56-", -1234.56),
])
def test_identifier_boundaries_preserve_complete_valid_currency_formats(value, expected):
    tokens = find_amount_tokens(f"PAGAMENTO REFERENCIA-85 {value} 2.000,00")
    assert [parse_amount_token(token) for token in tokens] == [expected, 2000]


@pytest.mark.parametrize("reference", ["080----85", "ABCDEF12-1234-4ABC-9876-ABCDEF0000A9"])
@pytest.mark.parametrize("suffix,expected", [("CR", 134.46), ("DB", -134.46)])
def test_declared_credit_debit_markers_preserve_identifier_boundaries(reference, suffix, expected):
    text = f"CLIENTE {reference} 134,46 {suffix}"
    tokens = find_profile_amount_tokens(text, negative_patterns=("{amount} DB",), positive_patterns=("{amount} CR",))
    assert len(tokens) == 1
    assert parse_amount_token(tokens[0]) == expected
    assert text[:tokens[0].start].strip().endswith(reference)
