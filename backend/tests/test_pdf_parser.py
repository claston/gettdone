import pytest

from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions


def test_parse_pdf_transactions_with_grouped_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    grouped_text = """
    06 NOV 2023 Total de entradas + 1.069,04
    Transferencia recebida pelo Pix CLIENTE A
    1.069,04
    14 NOV 2023 Total de saidas - 4.000,00
    Transferencia enviada pelo Pix FORNECEDOR B
    4.000,00
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [grouped_text])

    result = parse_pdf_transactions(b"%PDF synthetic grouped")

    assert result.layout.layout_name in {"nubank_statement_ptbr", "generic_statement_ptbr"}
    assert len(result.transactions) == 2
    assert any(item.amount > 0 for item in result.transactions)
    assert any(item.amount < 0 for item in result.transactions)


def test_parse_pdf_transactions_with_itau_inline_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    inline_text = """
    extrato conta / lancamentos
    data lancamentos valor (R$) saldo (R$)
    13/04/2026 PIX TRANSF ERICA S13/04 -2.835,00
    09/04/2026 TED 102.0001.ERICA S Y 6.000,00
    09/04/2026 SALDO DO DIA -1.307,48
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [inline_text])

    result = parse_pdf_transactions(b"%PDF synthetic inline")

    assert result.layout.layout_name in {"itau_statement_ptbr", "generic_statement_ptbr"}
    assert len(result.transactions) == 3
    assert any("SALDO DO DIA" in item.description.upper() for item in result.transactions)
    assert any(item.amount > 0 for item in result.transactions)
    assert any(item.amount < 0 for item in result.transactions)


def test_parse_pdf_transactions_raises_when_no_transaction_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: ["SEM DADOS DE MOVIMENTACOES"])

    with pytest.raises(
        InvalidFileContentError,
        match="unsupported table layout|no recognizable transaction row pattern",
    ):
        parse_pdf_transactions(b"%PDF-1.4 fake")
