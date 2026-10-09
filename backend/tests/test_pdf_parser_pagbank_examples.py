from __future__ import annotations

from typing import Any

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement

PAGBANK_EXAMPLE_CASES: dict[str, dict[str, Any]] = {
    "conta_com_saldo_diario": {
        "profile": "pagbank_extrato_conta_corrente_simples_v1",
        "amounts": [129.61, 226.32, 208.01, -814.0],
        "text": """
        PagBank 290 - PagSeguro Internet S/A Agencia 0001 Conta 83130701-2
        Extrato da conta Emitido em: 08/10/2026 as 14:37
        Periodo: 01/09/2026 a 30/09/2026
        Data Descricao Valor
        01/09/2026 Vendas - Disponivel PIX R$ 129,61
        01/09/2026 Vendas - Disponivel PIX R$ 226,32
        01/09/2026 Vendas - Disponivel DEBITO VISA R$ 208,01
        01/09/2026 Saldo do dia R$ 563,94
        02/09/2026 Pix enviado - Cliente Exemplo -R$ 814,00
        02/09/2026 Saldo do dia -R$ 458,07
        """,
    },
    "conta_corrente_simples": {
        "profile": "pagbank_extrato_conta_corrente_simples_v1",
        "amounts": [796.94, 3093.52],
        "text": """
        PagBank 290 - PagSeguro Internet S/A Agência Conta Corrente
        Extrato da conta corrente Período 01/06/2021 a 30/06/2021
        Data Descrição Valor
        05/06/2021 Plano R$ 796,94
        05/06/2021 Saldo do dia R$ 796,94
        06/06/2021 Plano R$ 3.093,52
        """,
    },
    "conta_bloqueada": {
        "profile": "pagseguro_relatorio_conta_bloqueada_v1",
        "amounts": [2.19, -2.19],
        "balances": [2.19, 0.0],
        "text": """
        PagSeguro CPF/CNPJ:
        DATA CÓDIGO DA TRANSAÇÃO DESCRIÇÃO CONTA VALOR (R$) SALDO (R$)
        08-11-2021 02:17:36 D297526165EA4501BCE0457B82 Bloqueio Bloqueado 2.19 2.19 1
        08-11-2021 02:50:27 C83A7AC6AA114979943E7F9A9 Desbloq Bloqueado -2.19 0 2
        """,
    },
    "transacoes_operacionais": {
        "profile": "pagbank_extrato_transacoes_operacionais_v1",
        "amounts": [-0.04, -0.04, -1.96],
        "text": """
        PagBank Extrato de transações Período 01/07/2020 a 31/07/2020
        Emitido em 12/02/2024 17:03
        Data da transação Data de liberação Código da transação Tipo da transação
        Nome Status NSU Parcelas Bandeira Valor bruto (R$) Taxa (R$) Valor líquido (R$)
        30/07/2020 01:44 - 5EDC1A41-551F-4A27-B398-98A16F034BC5 Saque
        GEVANDRO COMERCIO E Aprovada - - - 0.04 0.00 0.04
        29/07/2020 16:55 - BE08BEBE-71C9-4481-841D-F6F5076F300D Transferência
        GEVANDRO COMERCIO E Aprovada - - - 0.04 0.00 0.04
        02/07/2020 02:22 - 939B53BB-2C2B-490F-B424-8C7E330F8546 Saque
        GEVANDRO COMERCIO E Aprovada - - - 1.96 0.00 1.96
        """,
    },
}


@pytest.mark.parametrize("case_name", PAGBANK_EXAMPLE_CASES)
def test_parse_pdf_transactions_supports_pagbank_visual_examples(case_name: str, monkeypatch) -> None:
    case = PAGBANK_EXAMPLE_CASES[case_name]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [case["text"]])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [case["text"]])

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == case["profile"]
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    if "balances" in case:
        assert [transaction.running_balance for transaction in result.canonical_transactions] == case["balances"]
    ofx = build_ofx_statement(result.transactions)
    assert ofx.count("<STMTTRN>") == len(case["amounts"])
