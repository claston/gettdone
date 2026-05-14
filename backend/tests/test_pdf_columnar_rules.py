from app.application.normalization.pdf_columnar_rules import (
    apply_type_sign_hint,
    is_columnar_header_line,
    is_transaction_type_hint,
)


def test_is_transaction_type_hint_supports_credit_and_debit_prefixes() -> None:
    assert is_transaction_type_hint("debito") is True
    assert is_transaction_type_hint("credito estorno") is True
    assert is_transaction_type_hint("transferencia") is False


def test_apply_type_sign_hint_applies_expected_sign() -> None:
    assert apply_type_sign_hint(10.0, "DEBITO") == -10.0
    assert apply_type_sign_hint(-10.0, "CREDITO") == 10.0
    assert apply_type_sign_hint(10.0, "OUTRO") == 10.0


def test_is_columnar_header_line_matches_known_tokens() -> None:
    assert is_columnar_header_line("Descrição") is True
    assert is_columnar_header_line("valor (r$)") is True
    assert is_columnar_header_line("pix recebido") is False
