from app.application.normalization.pdf_inline_amount_rules import extract_single_trailing_amount_match


def test_extract_single_trailing_amount_match_parses_description_and_amount() -> None:
    result = extract_single_trailing_amount_match("Pagamento mercado 123,45")

    assert result is not None
    assert result.description == "Pagamento mercado"
    assert result.amount_token.value == "123,45"


def test_extract_single_trailing_amount_match_rejects_multiple_amounts() -> None:
    assert extract_single_trailing_amount_match("Compra 10,00 20,00") is None


def test_extract_single_trailing_amount_match_rejects_trailing_text_after_amount() -> None:
    assert extract_single_trailing_amount_match("Compra 10,00 saldo") is None
