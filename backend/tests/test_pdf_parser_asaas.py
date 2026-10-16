from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import (
    _resolve_closing_balance,
    _resolve_opening_balance,
)


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


def test_asaas_parser_keeps_rows_whose_description_precedes_the_visual_date() -> None:
    text = """
    ASAAS
    CNPJ Agência Conta
    Período 01 de Junho de 2026 a 30 de Junho de 2026
    Extrato gerado em 15/09/2026 às 13:10:59
    Saldo inicial do período
    R$ 3.832,51
    Saldo final do período
    R$ 3.826,52
    Data
    Movimentações
    Valor
    Cobrança recebida - fatura nr. 100000001 CLIENTE EXEMPLO
    01/06/2026
    R$ 100,00
    Taxa do Pix - fatura nr. 100000001 CLIENTE EXEMPLO
    01/06/2026
    R$ -1,99
    Taxa para Pix com dados manuais
    16/06/2026
    R$ -2,00
    17/06/2026
    R$ -2,00
    Taxa para Pix com dados manuais
    ASAAS Gestão Financeira Instituição de Pagamento S.A.
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "asaas_extrato_conta_digital_movimentacoes_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_asaas"
    assert [transaction.amount for transaction in result.transactions] == [100.0, -1.99, -2.0, -2.0]
    assert result.canonical_transactions is not None
    assert all(transaction.running_balance is None for transaction in result.canonical_transactions)
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_asaas_period_summary_resolves_opening_and_closing_balances() -> None:
    text = """
    Saldo inicial do período
    R$ 3.832,51
    Saldo final do período
    R$ 59.604,39
    """

    opening = _resolve_opening_balance(
        [],
        extracted_text=text,
        layout_name="asaas_extrato_conta_digital_movimentacoes_v1",
    )
    closing = _resolve_closing_balance(
        [],
        opening_balance=opening,
        layout_name="asaas_extrato_conta_digital_movimentacoes_v1",
        extracted_text=text,
    )

    assert opening == 3832.51
    assert closing == 59604.39
