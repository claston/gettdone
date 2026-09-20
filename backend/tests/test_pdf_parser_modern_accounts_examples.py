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
