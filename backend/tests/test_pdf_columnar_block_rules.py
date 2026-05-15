from app.application.normalization.pdf_columnar_block_rules import next_columnar_block_index


def test_next_columnar_block_index_without_balance_column() -> None:
    lines = ["01/10/2024", "PIX RECEBIDO", "CREDITO", "10,00", "02/10/2024"]
    assert next_columnar_block_index(lines, current_index=0) == 4


def test_next_columnar_block_index_with_balance_column() -> None:
    lines = ["01/10/2024", "PIX RECEBIDO", "CREDITO", "10,00", "100,00", "02/10/2024"]
    assert next_columnar_block_index(lines, current_index=0) == 5
