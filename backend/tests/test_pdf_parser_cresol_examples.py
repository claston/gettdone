from __future__ import annotations

from typing import Any

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement

CRESOL_EXAMPLE_CASES: dict[str, dict[str, Any]] = {
    "conta_corrente_legado_sinal_sufixo": {
        "profile": "cresol_extrato_conta_corrente_legado_sinal_sufixo_v1",
        "amounts": [-3500.10, 3908.00, -238.67, 124945.00],
        "dates": ["2020-11-03", "2020-11-04", "2020-11-05", "2020-11-05"],
        "text": """
        CRESOL
        Extrato de Conta Corrente
        Instituição Financeira: Cresol
        Agência: 0001 Conta: 12345-6
        Período de 01/11/2020 a 30/11/2020
        Data Histórico Valor
        Saldo Anterior R$ 24.327,80 +
        03/11/2020 PGTO JUROS DE EMPRÉSTIMO ROTATIVO R$ 3.500,10 -
        04/11/2020 TRANSF. ENTRE CONTAS DE TITULARIDADE DIFERENTE-D PAG. 1 R$ 3.908,00 +
        05/11/2020 JUROS DE CHEQUE ESPECIAL R$ 238,67 -
        05/11/2020 TED CRÉDITO 237 0457 15828064000152 R$ 124.945,00 +
        """,
    },
    "conta_corrente_consolidado_cd": {
        "profile": "cresol_extrato_consolidado_conta_corrente_valor_cd_v1",
        "amounts": [-300.00, 1849.66, -450.00, -18754.65, -319.53],
        "dates": ["2020-01-06", "2020-01-07", "2020-01-07", "2020-01-14", "2020-01-14"],
        "text": """
        CRESOL
        EXTRATO CONSOLIDADO DE CONTA CORRENTE
        Agência: 0001 Conta: 12345-6 Segundo Titular: Conta Integração:
        Período: 01/01/2020 a 31/12/2020 Data/Hora: 21/10/2021
        Data Movimento Lançamento Identificação Valor
        06/01/2020 SALDO ANTERIOR 33,11 C
        06/01/2020 CHEQUE COMPENSADO 1110 300,00 D
        07/01/2020 TED CRÉDITO 237 0661 02806382000170 1.849,66C
        07/01/2020 CHEQUE PAGO POR CAIXA 1111 450,00 D
        14/01/2020 TED DÉBITO - CANAIS ELETRÔNICOS 756 3271 35009225204 18.754,65 D
        14/01/2020 DÉBITO DE ARRECADAÇÕES 10890014012002806 319,53 D
        """,
    },
    "conta_corrente_lista_saldo_dia": {
        "profile": "cresol_extrato_lancamentos_saldo_dia_pix_credito_v1",
        "amounts": [1550.00, 170.00],
        "dates": ["2022-09-26", "2022-09-26"],
        "text": """
        CRESOL
        Saldo em Conta R$ 2.354,41 Limite de R$ 0,00 Saldo R$ 2.354,41
        01 de Setembro de 2022 a 30 de Setembro de 2022
        Lançamentos
        26/09/2022 Saldo do dia: + R$ 2.354,41
        DIFERENTE-D Transferência FEDERAÇÃO DOS TRAB - + R$ 1.550,00
        DIFERENTE-D Transferência COOPERATIVA REGIONAL - + R$ 170,00
        """,
    },
    "rdc_renda_fixa": {
        "profile": "cresol_extrato_rdc_renda_fixa_v1",
        "amounts": [53.33, 234.70, 43.82, 108.67],
        "dates": ["2022-11-30", "2022-11-30", "2022-11-30", "2022-11-30"],
        "text": """
        CRESOL
        Extrato de RDC - (Renda Fixa)
        Instituição Financeira: Cresol Agência: 0001 Conta: 12345-6
        Consulta Posição Consolidada em 30/11/2022
        Data Histórico Valor
        30/11/2022 PREVISÃO DE CORREÇÃO PROVISÓRIA DE APLICAÇÃO A CREDITAR + R$ 53,33
        30/11/2022 PREVISÃO DE CORREÇÃO PROVISÓRIA DE APLICAÇÃO A CREDITAR + R$ 234,70
        30/11/2022 PREVISÃO DE CORREÇÃO PROVISÓRIA DE APLICAÇÃO A CREDITAR + R$ 43,82
        30/11/2022 PREVISÃO DE CORREÇÃO PROVISÓRIA DE APLICAÇÃO A CREDITAR + R$ 108,67
        """,
    },
    "conta_corrente_lista_pix": {
        "profile": "cresol_extrato_conta_corrente_moderno_pix_v1",
        "amounts": [-1000.00, -13.90, -97.00, 15000.00],
        "dates": ["2026-03-02", "2026-03-02", "2026-03-02", "2026-03-02"],
        "text": """
        CRESOL
        Agência: 0001 Conta: 12345-6
        Extrato de conta corrente
        Conta Corrente R$ 164.206,51 Limite de crédito R$ 0,00 Disponível R$ 164.206,51
        Consulta posição consolidada em: Período de 01/02/2026 a 02/03/2026
        Lançamentos
        02/03/2026 Saldo do dia: + R$ 164.206,51
        PIX DÉBITO PARA: - R$ 1.000,00
        PIX DÉBITO PARA: - R$ 13,90
        PIX DÉBITO PARA: - R$ 97,00
        PIX CRÉDITO DE: + R$ 15.000,00
        """,
    },
    "ofx_simples_posicao_consolidada": {
        "profile": "cresol_ofx_simples_posicao_consolidada_v1",
        "amounts": [-1430.00, 2000.00, -50.00, -5.00, -500.00, 1045.00],
        "dates": [
            "2026-08-28",
            "2026-08-28",
            "2026-08-10",
            "2026-08-10",
            "2026-08-06",
            "2026-08-04",
        ],
        "descriptions": [
            "PIX DEBITO PARA: CONTRAPARTE A",
            "PIX CREDITO DE: CONTRAPARTE B",
            "INTEGRALIZACAO PROGRAMA",
            "PIX DEVOLUCAO ENVIADA PARA: MANUELLY DOS SANTOS - 09/08",
            "PIX DEBITO INTERCOOPERATIVO PARA",
            "CREDITO TITULOS COBRANCA PROPRIA(F)",
        ],
        "text": """
        [TITULAR]
        AGENCIA [AGENCIA] CONTA [CONTA]
        SALDO EM CONTA [CONTA] DE CREDITO SALDO DISPONIVEL
        R$ 1.716,47 R$ 0,00 R$ 1.716,47
        28/08/2026 SALDO DO DIA: + R$ 1.716,47
        28/08/2026 PIX DEBITO PARA: CONTRAPARTE A - R$ 1.430,00
        28/08/2026 PIX CREDITO DE: CONTRAPARTE B + R$ 2.000,00
        10/08/2026 INTEGRALIZACAO PROGRAMA - R$ 50,00
        10/08/2026 PIX DEVOLUCAO ENVIADA PARA: MANUELLY - R$ 5,00
        DOS SANTOS - 09/08
        06/08/2026 PIX DEBITO INTERCOOPERATIVO PARA: - R$ 500,00
        04/08/2026 CREDITO TITULOS COBRANCA PROPRIA(F) + R$ 1.045,00
        CONSULTA POSICAO CONSOLIDADA EM 14/09/2026 AS 21:28:47
        PERIODO DE 01/08/2026 A 31/08/2026
        PAGINA 1 DE 1
        """,
    },
}


@pytest.mark.parametrize("case_name", CRESOL_EXAMPLE_CASES)
def test_parse_pdf_transactions_supports_cresol_visual_examples(case_name: str, monkeypatch) -> None:
    case = CRESOL_EXAMPLE_CASES[case_name]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [case["text"]])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [case["text"]])

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == case["profile"]
    assert result.parse_metrics["selected_parser"] == "layout_specific_cresol"
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    assert [transaction.date for transaction in result.transactions] == case["dates"]
    if "descriptions" in case:
        assert [transaction.description for transaction in result.transactions] == case["descriptions"]
    assert all("SALDO" not in transaction.description.upper() for transaction in result.transactions)

    ofx = build_ofx_statement(result.transactions)
    assert ofx.count("<STMTTRN>") == len(case["amounts"])
    assert all(f"<TRNAMT>{amount:.2f}" in ofx for amount in case["amounts"])
