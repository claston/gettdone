from app.application import pdf_parser as pdf_parser_module

PAN_STATEMENT_TEXT = """
Olá, CLIENTE!
Esse é o seu extrato
da sua conta Pan.
Período do extrato: 08/07 a 07/08
Saldo nesse período: R$ 1.517,83
Entrou na sua conta: R$ 9.483,79 Saiu da sua conta: R$ 8.356,97
Pagamentos feitos: 0 pagamento Transferências feitas: 0 transferência
Saques feitos: 0 saque Total em tarifas: R$ 0,00
CLIENTE TESTE
Agência 0001 - Conta 0106819340
Extrato de 08/07/2024 a 07/08/2024
Suas movimentações:
Agosto de 2024
CREDITO PIX
07/08 909928343 PIX RECEBIDO + R$ 100,00 R$ 1.517,83
DEBITO PIX
06/08 906233919 PIX ENVIADO - R$ 20,50 R$ 1.417,83
DEBITO PIX
05/08 999521941 PIX ENVIADO - R$ 160,00 R$ 1.438,33
Extrato emitido em 07/08/2024 às 19:09h Página 2 de 10
"""


def test_parse_banco_pan_descending_statement_without_false_balance_warnings() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([PAN_STATEMENT_TEXT])

    assert result.layout.layout_name == "banco_pan_extrato_conta_pix_saldo_v1"
    assert result.layout.confidence >= 0.95
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert [transaction.amount for transaction in result.transactions] == [100.0, -20.5, -160.0]
    assert [row.running_balance for row in result.canonical_transactions] == [1517.83, 1417.83, 1438.33]
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_count"] == 0
