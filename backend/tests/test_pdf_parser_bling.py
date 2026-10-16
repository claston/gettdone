from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import (
    _resolve_closing_balance,
    _resolve_opening_balance,
)


def test_bling_cash_report_parses_entries_and_exits_without_summary_rows() -> None:
    pages = [
        """
        Bling - Relatório de Caixa e Bancos
        Bling Conta - Todas categorias - Período de 01/01/2026 até 31/01/2026.
        Data
        Cliente/Fornecedor
        Histórico
        Categoria
        Entrada
        Saída
        Saldo do dia 3.212,25 (S)
        29/01/2026
        Ref. a NF nº 002327
        1,95
        29/01/2026
        CLIENTE EXEMPLO
        Ref. a NF nº 002327
        Vendas de produtos
        260,00
        Saldo do dia 2.954,20 (S)
        Pagamento de conta. - Pagamento com Pix - Transferência Pix
        20/01/2026
        FORNECEDOR EXEMPLO
        3.552,00
        CLIENTE SEM CATEGORIA
        20/01/2026
        Ref. a NF nº 002272
        1.276,14
        """,
        """
        Bling - Relatório de Caixa e Bancos
        Pagamento de conta. - Pagamento com Pix - Transferência Pix
        03/01/2026
        FORNECEDOR EXEMPLO
        500,00
        Saldo do dia 6.068,60 (S).
        Totais
        6.104,44
        8.960,79
        Diferença (Entrada - Saída)
        -2.856,35
        Saldo Anterior
        6.068,60
        Saldo Final
        3.212,25
        """,
    ]

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)

    assert result.layout.layout_name == "bling_relatorio_caixa_bancos_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_bling"
    assert [transaction.amount for transaction in result.transactions] == [
        -1.95,
        260.0,
        -3552.0,
        1276.14,
        -500.0,
    ]
    assert all("DIFERENÇA" not in transaction.description.upper() for transaction in result.transactions)
    assert result.canonical_transactions is not None
    assert all(transaction.running_balance is None for transaction in result.canonical_transactions)


def test_bling_summary_resolves_opening_and_closing_balances() -> None:
    text = """
    Totais
    6.104,44
    8.960,79
    Diferença (Entrada - Saída)
    -2.856,35
    Saldo Anterior
    6.068,60
    Saldo Final
    3.212,25
    """

    opening = _resolve_opening_balance(
        [],
        extracted_text=text,
        layout_name="bling_relatorio_caixa_bancos_v1",
    )
    closing = _resolve_closing_balance(
        [],
        opening_balance=opening,
        layout_name="bling_relatorio_caixa_bancos_v1",
        extracted_text=text,
    )

    assert opening == 6068.6
    assert closing == 3212.25
