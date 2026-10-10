from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module

SANTANDER_CONSOLIDATED_BASIC_LAYOUT = "santander_extrato_consolidado_basico_conta_corrente_v1"


def _sample_pages() -> list[str]:
    return [
        """
        EXTRATO CONSOLIDADO
        janeiro/2024
        Extrato_PJ_A4_Basico - 25/5/2023
        """,
        """
        Resumo - janeiro/2024
        Conta Corrente
        Movimentacao
        Data                  Descricao                         N Documento        Movimento (R$)       Saldo (R$)
        SALDO EM 31/12                                                                                       0,00
        02/01                 PIX RECEBIDO                      123456                    34,38
                              PAGAMENTO CARTAO DE DEBITO        180915                    87,79
                              GETNET-MAESTRO
                              TARIFA PIX RECEBIDO QR CHECKOUT   -                          1,00-
                              APLICACAO CONTAMAX                -                        121,17-             0,00
        03/01                 TARIFA PIX RECEBIDO QR CHECKOUT   -                          2,00-
                              02/01/2024
                              PAGAMENTO CARTAO DE DEBITO        180915                    10,00
                              GETNET-VISA ELECTR
                              APLICACAO CONTAMAX                -                          8,00-             0,00
        SALDOS POR PERIODO
        """,
    ]


def test_parses_santander_basic_card_settlements_and_ignores_reference_dates() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        _sample_pages(),
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == SANTANDER_CONSOLIDATED_BASIC_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_santander_consolidated_basic"
    assert [row.date for row in result.transactions] == [
        "2024-01-02",
        "2024-01-02",
        "2024-01-02",
        "2024-01-02",
        "2024-01-03",
        "2024-01-03",
        "2024-01-03",
    ]
    assert [row.amount for row in result.transactions] == [
        34.38,
        87.79,
        -1.0,
        -121.17,
        -2.0,
        10.0,
        -8.0,
    ]
    assert result.transactions[1].description == "PAGAMENTO CARTAO DE DEBITO GETNET-MAESTRO"
    assert result.transactions[5].description == "PAGAMENTO CARTAO DE DEBITO GETNET-VISA ELECTR"
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
