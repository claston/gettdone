from app.application import pdf_parser as pdf_parser_module


def test_asaas_parser_excludes_opening_balance_and_footer_from_transactions() -> None:
    text = """
    CNPJ
    Agência: 0001
    Conta: 123456 - 7
    Período
    01 de Março de 2026 a 31 de Março de 2026
    Extrato gerado em 01/04/2026 às 18:20:00
    Saldo inicial do período
    Saldo final do período
    R$ 1.000,00
    R$ 1.098,01
    Data
    Movimentações
    Valor
    04/03/2026
    Comissão recebida do parceiro - fatura nr.
    733619991
    R$ 100,00
    05/03/2026
    Taxa de boleto - fatura nr. 733619991
    R$ -1,99
    Valor
    ASAAS Gestão Financeira Instituição de Pagamento S.A.
    CNPJ: 19.540.550/0001-21
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "asaas_extrato_conta_digital_movimentacoes_v1"
    assert [transaction.amount for transaction in result.transactions] == [100.0, -1.99]
    assert [transaction.description for transaction in result.transactions] == [
        "Comissão recebida do parceiro - fatura nr. 733619991",
        "Taxa de boleto - fatura nr. 733619991",
    ]
    assert all(transaction.description != "SALDO INICIAL" for transaction in result.transactions)
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
