from app.application import pdf_parser as pdf_parser_module


def test_parse_bank_agency_statement_preserves_date_across_pages_and_skips_total() -> None:
    first_page = """
    Extrato
    Período de 01/09/2026 a 30/09/2026
    Cliente: EMPRESA EXEMPLO
    Banco: 077 Agência: 0001-0 Conta: 12345-6
    Data
    Descrição
    Documento
    Crédito (R$)
    Débito (R$)
    Saldo (R$)
    Saldo anterior
    100,00
    22/09/2026
    PIX RECEBIDO
    123456
    10,00
    110,00
    """
    second_page = """
    Data
    Descrição
    Documento
    Crédito (R$)
    Débito (R$)
    Saldo (R$)
    PAGAMENTO FORNECEDOR
    654321
    -5,00
    105,00
    23/09/2026
    PIX RECEBIDO
    777777
    20,00
    125,00
    Total
    30,00
    125,00
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([first_page, second_page])

    assert result.layout.layout_name == "bank_agency_debit_credit_statement_v1"
    assert result.layout.confidence >= 0.95
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert [transaction.date for transaction in result.transactions] == [
        "2026-09-22",
        "2026-09-22",
        "2026-09-23",
    ]
    assert [transaction.amount for transaction in result.transactions] == [10.0, -5.0, 20.0]
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        110.0,
        105.0,
        125.0,
    ]
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_count"] == 0
