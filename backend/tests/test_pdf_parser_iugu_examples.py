from app.application import pdf_parser as pdf_parser_module


def test_parse_pdf_transactions_supports_iugu_bank_movement_export() -> None:
    pages = [
        """
        Filtros utilizados:
        - Conta Corrente: 0000/IDeveloper-IUGU - IDEVELOPER-IUGU /
        - Período Inicio: 01/10/2026
        - Período Fim: 31/10/2026
        Total de 3 registro(s) encontrado(s).
        SALDO ANTERIORR$
        100,00
        CODIGODATA
        MOV. STATUS COBRANÇAIDENTIFICAÇÃO HISTÓRICO PARCELAVALOR SALDO #
        #1001
        01/10/2026
        às
        05:56:15
        Autorizado2179302 98A33EB863814F76BD13F9445D8E19AE
        Entrada - Doação
        Nº 10 - Cliente Alfa
        1/1 R$
        20,00
        R$
        120,00
        #1002
        01/10/2026
        às
        05:56:15
        Autorizado2179302 98A33EB863814F76BD13F9445D8E19AE
        Tarifa - Doação
        Nº 10 - Cliente Alfa
        1/1 R$ -0,62R$
        119,38
        Missão Exemplo
        Movimentação Bancária
        """,
        """
        SALDO ANTERIORR$
        100,00
        #1003
        02/10/2026
        às
        09:10:45
        Autorizado2190809 B01ADC6320FD47B18F8A366B3D937883
        Depósito - Cobrança Avulsa
        Cliente Beta
        1/1 R$
        50,00
        R$
        169,38
        """,
    ]

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)

    assert result.layout.layout_name == "iugu_movimentacao_bancaria_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_iugu_movimentacao_bancaria"
    assert [(transaction.date, transaction.amount) for transaction in result.transactions] == [
        ("2026-10-01", 20.0),
        ("2026-10-01", -0.62),
        ("2026-10-02", 50.0),
    ]
    assert [transaction.external_reference_id for transaction in result.canonical_transactions] == [
        "1001",
        "1002",
        "1003",
    ]
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        120.0,
        119.38,
        169.38,
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0
