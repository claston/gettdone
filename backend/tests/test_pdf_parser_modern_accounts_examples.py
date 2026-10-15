from __future__ import annotations

from typing import Any

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement

MODERN_ACCOUNT_CASES: dict[str, dict[str, Any]] = {
    "bradesco_empresas_negocios": {
        "profile": "bradesco_empresas_negocios_extrato_lancamentos_v1",
        "amounts": [35.90, 9.00, -120.00],
        "balances": [9461.39, 9269.19, 9149.19],
        "text": """
        bradesco empresas e negócios
        Extrato
        Data e hora: 05/05/2026
        Período: 01/04/2026 a 30/04/2026
        Busca de lançamentos
        Dados da empresa Agência Conta
        Lançamentos
        Data Descrição Documento Tipo Valor Saldo (R$)
        30/04/2026 PIX QR CODE ESTATIC REM: EMPRESA UM 1813055 Crédito 35,90 9.461,39
        29/04/2026 PIX QR CODE ESTATIC REM: EMPRESA DOIS 1400501 Crédito 9,00 9.269,19
        28/04/2026 PAGAMENTO FORNECEDOR 9912 Débito 120,00 9.149,19
        """,
    },
    "sicredi_moderno": {
        "profile": "sicredi_extrato_conta_corrente_moderno_movimentacoes_v1",
        "amounts": [-800.00, -840.13, 125.50],
        "text": """
        Sicredi
        Momento de emissão Período das movimentações
        Extrato de conta corrente
        Titular - CPF Dados da conta Cooperativa Conta
        Saldo em conta Saldo bloqueado Lançamento a conferir Bloqueio judicial
        Lançamentos futuros a partir de hoje, 01/04/2026
        01/04/2026 Encargos de cheque especial - R$ 12,75
        Movimentações de 01/03/2026 a 31/03/2026
        Data Descrição Valor
        02/03/2026 PAGAMENTO PIX - - R$ 800,00
        02/03/2026 PAGAMENTO BOLETO - R$ 840,13
        03/03/2026 PIX RECEBIDO + R$ 125,50
        """,
    },
    "picpay_grouped_2025": {
        "profile": "picpay_extrato_conta_grouped_2025_v1",
        "amounts": [-65.00, 50.00, -30.00],
        "text": """
        PicPay
        Agência Conta
        Extrato de conta
        Período 1 de janeiro de 2025 a 1 de janeiro de 2026
        Saldo final do período R$ 1,00
        31 de dezembro de 2025 Saldo ao final do dia: R$ 1,00
        Hora Tipo Origem / Destino Forma de pagamento Valor
        18:02 Pix enviado PESSOA UM Com saldo -R$ 65,00
        17:53 Pix recebido PESSOA DOIS Com saldo +R$ 50,00
        30 de dezembro de 2025 Saldo ao final do dia: R$ 16,00
        21:43 Pagamento para EMPRESA TRES Com saldo -R$ 30,00
        """,
    },
    "neon_moderno": {
        "profile": "neon_extrato_por_periodo_moderno_v1",
        "amounts": [15.00, -60.00, -30.00],
        "balances": [85.00, 70.00, 130.00],
        "text": """
        neon Conta digital Neon Pagamentos S.A.
        Extrato por período
        Ano Base: 2026
        Período de 05/11/2025 a 06/02/2026
        Cliente Agência bancária Conta-corrente
        Descrição Data Hora Valor Saldo Cartão
        Pix recebido de EMPRESA UM 06/02/2026 09:50 R$ 15,00 R$ 85,00 -
        Pix enviado para PESSOA DOIS 05/02/2026 10:15 -R$ 60,00 R$ 70,00 -
        Pagamento Fatura 04/02/2026 08:00 -R$ 30,00 R$ 130,00 1234
        """,
    },
    "banco_bv_grouped": {
        "profile": "banco_bv_extrato_periodo_grouped_v1",
        "amounts": [1605.77, -1605.97, 41.00, -41.00],
        "text": """
        Banco BV S.A.
        esse é o extrato do período:
        10 de Agosto de 2025 a 10 de Fevereiro de 2026
        Banco: 0413 Agência Conta CPF
        17 de Outubro de 2025
        15:15 Pix recebido de EMPRESA UM R$ 1.605,77
        15:17 Aplicação em CDB -R$ 1.605,97
        23 de Outubro de 2025
        14:25 Resgate de CDB R$ 41,00
        14:26 Pix enviado para PESSOA DOIS -R$ 41,00
        """,
    },
}


@pytest.mark.parametrize("case_name", MODERN_ACCOUNT_CASES)
def test_parse_pdf_transactions_supports_modern_account_layouts(case_name: str, monkeypatch) -> None:
    case = MODERN_ACCOUNT_CASES[case_name]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [case["text"]])
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [case["text"]],
    )

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == case["profile"]
    assert result.parse_metrics["selected_parser"] == "layout_specific_modern_accounts"
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    if "balances" in case:
        assert [transaction.running_balance for transaction in result.canonical_transactions] == case["balances"]

    ofx = build_ofx_statement(result.transactions)
    assert ofx.count("<STMTTRN>") == len(case["amounts"])
    assert all(f"<TRNAMT>{amount:.2f}" in ofx for amount in case["amounts"])


def test_parse_pdf_transactions_joins_split_bradesco_visual_rows(monkeypatch) -> None:
    text = """
    Bradesco empresas e negócios
    Extrato
    Data e hora: 08/10/2026
    Busca de lançamentos
    Dados da empresa Agência Conta
    Lançamentos
    Data Descrição Documento Tipo Valor Saldo (R$)
    25/09/2026 TRANSFERÊNCIA RECEBIDA
    REMETENTE EMPRESA EXEMPLO 25/09 123456 Crédito 2.000,00 5.721,78
    25/09/2026 TRANSFERÊNCIA RECEBIDA
    REMETENTE EMPRESA EXEMPLO 25/09 123457 Crédito 2.000,00 7.721,78
    24/09/2026 PIX RECEBIDO
    REMETENTE EMPRESA EXEMPLO 24/09 654320 Crédito 100,00 10.000,00
    24/09/2026 PAGAMENTO FORNECEDOR
    FAVORECIDO EMPRESA EXEMPLO 24/09 654321 Débito 300,00 9.700,00
    PIX ENVIADO 23/09/2026 777777 Débito 1.180,80 8.519,20
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [text])
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [text],
    )

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "bradesco_empresas_negocios_extrato_lancamentos_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_modern_accounts"
    assert [transaction.amount for transaction in result.transactions] == [
        2000.00,
        2000.00,
        100.00,
        -300.00,
        -1180.80,
    ]
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        5721.78,
        7721.78,
        10000.00,
        9700.00,
        8519.20,
    ]
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["confidence_band"] == "high"
    assert result.parse_metrics["export_recommendation"] == "safe_to_export"


def test_banco_bv_current_account_parses_column_major_daily_groups() -> None:
    pages = [
        """
        Banco BV
        Extrato de Conta Corrente
        Período: 01/10/2026 à 14/10/2026
        Data
        14/10/2026
        13/10/2026
        Lançamento
        Saldo do dia
        Débito por TED IB
        Transferência recebida
        Crédito de cobrança
        Saldo do dia
        Débito por TED IB
        Transferência recebida
        Tarifa de liquidação de título
        Tarifa de liquidação de título
        Tarifa de liquidação de título
        Tarifa de liquidação de título
        Nº Documento
        885078361
        498320577
        885012474
        884977064
        498208604
        498171376
        498171243
        498170991
        498170987
        Valor (R$)
        2.312,09
        - 228.000,00
        208.009,91
        20.650,84
        1.651,34
        - 93.000,00
        77.740,15
        - 65,00
        - 10,40
        - 4,00
        - 2,00
        """,
        """
        Banco BV
        Extrato de Conta Corrente
        10/10/2026
        09/10/2026
        Saldo do dia
        Crédito de cobrança
        Saldo do dia
        Tarifa de registro de títulos
        Cobrança própria
        884700001
        497700002
        16.992,59
        15.287,19
        1.705,40
        - 70,20
        """,
    ]

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)

    assert result.layout.layout_name == "banco_bv_extrato_conta_corrente_saldo_dia_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_modern_accounts"
    assert [transaction.amount for transaction in result.transactions] == [
        -228000.0,
        208009.91,
        20650.84,
        -93000.0,
        77740.15,
        -65.0,
        -10.4,
        -4.0,
        -2.0,
        15287.19,
        -70.2,
    ]
    assert [transaction.date for transaction in result.transactions] == [
        "2026-10-14",
        "2026-10-14",
        "2026-10-14",
        "2026-10-13",
        "2026-10-13",
        "2026-10-13",
        "2026-10-13",
        "2026-10-13",
        "2026-10-13",
        "2026-10-10",
        "2026-10-09",
    ]
    assert result.canonical_transactions is not None
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        2312.09,
        None,
        None,
        1651.34,
        None,
        None,
        None,
        None,
        None,
        16992.59,
        1705.4,
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0
