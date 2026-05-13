from app.application.normalization.pdf_columnar_row_rules import is_valid_columnar_transaction_row


def test_is_valid_columnar_transaction_row_accepts_valid_triplet() -> None:
    assert is_valid_columnar_transaction_row(description="PIX RECEBIDO", type_raw="CREDITO", amount_raw="10,00") is True


def test_is_valid_columnar_transaction_row_rejects_header_line() -> None:
    assert is_valid_columnar_transaction_row(description="DESCRIÇÃO", type_raw="CREDITO", amount_raw="10,00") is False


def test_is_valid_columnar_transaction_row_rejects_invalid_type_or_amount() -> None:
    assert is_valid_columnar_transaction_row(description="PIX", type_raw="OUTRO", amount_raw="10,00") is False
    assert is_valid_columnar_transaction_row(description="PIX", type_raw="DEBITO", amount_raw="abc") is False
