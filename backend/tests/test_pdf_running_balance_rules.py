from app.application.normalization.pdf_amount_tokens import AmountToken
from app.application.normalization.pdf_running_balance_rules import parse_running_balance


def test_parse_running_balance_returns_none_without_token() -> None:
    assert parse_running_balance(None) is None


def test_parse_running_balance_parses_amount_value() -> None:
    token = AmountToken(value="1.487,66", start=0, end=8)
    assert parse_running_balance(token) == 1487.66
