# ruff: noqa: E501

from app.application import pdf_parser as pdf_parser_module


def test_parse_pdf_transactions_supports_bradesco_net_empresa_vertical_rows() -> None:
    pages = [
        """
        Folha 1/2
        Agência | Conta                   Total Disponível (R$)                Total (R$)
        00468 | 0255774-6                 4.022,10                            4.022,10
        Extrato de: Ag: 468 | CC: 0255774-6 | Entre 01/09/2026 e 30/09/2026
        Data              Lançamento                                        Dcto.               Crédito (R$)                Débito (R$)                Saldo (R$)
        31/08/2026        SALDO ANTERIOR                                                                                                                     4.022,10
        01/09/2026        TED-TRANSF ELET DISPON                            5920926                     2.490,32                                             6.512,42
                          REMET.CLIENTE ALFA
                          RECEB POR FORNECIMENTO                            101                          655,69                                              7.168,11
                          CLIENTE BETA
                          TARIFA BANCARIA                                   18                                                      -72,00                  7.096,11
                          PAGAMENTO FUNCs NET EMPRESA
        """,
        """
        Folha 2/2
        02/09/2026TARIFA OPERACAO CREDITO80626                                                                         -10.000,00-2.903,89
                    CUSTO REG CONTRATO VEIC                        80626                                              -350,00-3.253,89
                    CONTRATO REPASSE BNDES
        Total                                                                                 3.146,01              -5.422,00                -3.253,89
        Saldos Invest Fácil / Plus
        Data Histórico Valor (R$)
        02/09/2026 SALDO INVEST FÁCIL 1.473,39
        """,
    ]

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        pages,
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == "bradesco_net_empresa_extrato_mensal_por_periodo_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_bradesco_net_empresa"
    assert [(transaction.date, transaction.amount) for transaction in result.transactions] == [
        ("2026-09-01", 2490.32),
        ("2026-09-01", 655.69),
        ("2026-09-01", -72.0),
        ("2026-09-02", -10000.0),
        ("2026-09-02", -350.0),
    ]
    assert [transaction.external_reference_id for transaction in result.canonical_transactions] == [
        "5920926",
        "101",
        "18",
        "80626",
        "80626",
    ]
    assert result.transactions[0].description == "TED-TRANSF ELET DISPON 5920926 REMET.CLIENTE ALFA"
    assert result.transactions[-1].description == "CUSTO REG CONTRATO VEIC 80626 CONTRATO REPASSE BNDES"
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        6512.42,
        7168.11,
        7096.11,
        -2903.89,
        -3253.89,
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all("SALDO INVEST" not in transaction.description for transaction in result.transactions)
