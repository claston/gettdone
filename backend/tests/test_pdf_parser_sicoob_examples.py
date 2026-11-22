from __future__ import annotations

from typing import Any

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import _resolve_ofx_account_type
from app.application.ofx_writer import build_ofx_statement
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext

SICOOB_EXAMPLE_CASES: dict[str, dict[str, Any]] = {
    "fatura_cartao": {
        "profile": "sicoob_fatura_cartao_credito_movimentos_v1",
        "amounts": [3673.51, -10.90, -225.0, -8.07, -135.17, -141.75, -247.50],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        SISBR - SISTEMA DE INFORMÁTICA DO SICOOB
        EXTRATO DE FATURA DE CARTÃO DE CRÉDITO
        Conta Cartão Fatura de JANEIRO Vencimento: 03/01/2022 Mastercard
        Movimentos
        - SALDO ANTERIOR 3.673,51
        03/12 PAGAMENTO-BOLETO BANCARIO -3.673,51
        18/12 ANUIDADE MASTERCARD 10,90
        08/07 GS CELULARES E 06/06 225,00
        08/07 MERCPAGO*INFOPARSP 06/12 8,07
        20/10 SUCAL 02/02 135,17
        20/10 STARMARK MAQ COSTURA 02/02 141,75
        25/10 DIST DE EMB PLASTIL 02/02 247,50
        """,
    },
    "conta_historico_2021_janeiro": {
        "profile": "sicoob_sisbr_extrato_conta_corrente_historico_movimentacao_v1",
        "amounts": [-580.50, 50.0, -1305.0, -714.50, -711.0, -612.0],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        EXTRATO CONTA CORRENTE COOP.: CONTA: PERÍODO: 01/01/2021 - 31/01/2021
        HISTÓRICO DE MOVIMENTAÇÃO DATA HISTÓRICO VALOR
        31/12 SALDO ANTERIOR 35,13C
        31/12 SALDO BLOQ.ANTERIOR 0,00*
        05/01 CHEQUE PAGO CAIXA DOC.: 000.60 580,50D
        05/01 DEP.DINHEIRO CPF: ENVELOPE: 0685458010 DOC.: 57 50,00C
        05/01 CHEQUE PAGO CAIXA DOC.: 000.380 1.305,00 D
        05/01 CH COOP/AG.DEP.CTA DOC.: 000.605 714,50D
        05/01 CH COOP/AG.DEP.CTA DOC.: 000.605 711,00D
        05/01 CH COOP/AG.DEP.CTA DOC.: 000.378 612,00D
        """,
    },
    "conta_documento_2021": {
        "profile": "sicoob_sisbr_extrato_conta_corrente_monospace_valor_dc_v1",
        "amounts": [273.82, 411.60, 22.0, 46788.93, 1856.0, 4500.0, 700.0, 3250.0],
        "text": """
        SICOOB - Sistema de Cooperativas de Crédito do Brasil
        SISBR - SISTEMA DE INFORMÁTICA DO SICOOB EXTRATO CONTA CORRENTE
        COOP.: CONTA: DATA DOCUMENTO HISTÓRICO VALOR
        31/12/2020 SALDO ANTERIOR 45.818,18C
        31/12/2020 SALDO BLOQUEADO ANTERIOR 0,00*
        04/01/2021 370691404 CR COMPRAS MASTERCARD 273,82C
        04/01/2021 182944771 CRÉD.TED-STR 411,60C CODIGO TED: T651877092
        04/01/2021 2571650 CRÉD.TRANSF.CONTAS 22,00C
        04/01/2021 116 DEP.CHEQUE BLOQ.1D 46.788,93*
        04/01/2021 4 DEP CHEQUE COOP/AG 1.856,00C
        04/01/2021 4 DEP CHEQUE COOP/AG 4.500,00C
        04/01/2021 4 DEP CHEQUE COOP/AG 700,00C
        04/01/2021 4 DEP CHEQUE COOP/AG 3.250,00C
        """,
    },
    "conta_historico_2021_fevereiro": {
        "profile": "sicoob_sisbr_extrato_conta_corrente_historico_movimentacao_v1",
        "amounts": [1628.33, 906.59, 1414.43],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        EXTRATO CONTA CORRENTE COOP.: CONTA: PERÍODO: 01/02/2021 - 28/02/2021
        HISTÓRICO DE MOVIMENTAÇÃO DATA HISTÓRICO VALOR
        29/01 SALDO ANTERIOR 12.058,67C
        29/01 SALDO BLOQ.ANTERIOR 0,00*
        01/02 CRED.TR.CT.INTERCRE REM.: DOC.: 31 1.628,33C
        01/02 CR CMP VISA SIPAG_Cred._Visa DOC.: 383060911 906,59C
        01/02 CR CMP MSTD SIPAG_Cred._Mastercard DOC.: 383060912 1.414,43C
        """,
    },
    "conta_documento_2018": {
        "profile": "sicoob_sisbr_extrato_conta_corrente_monospace_valor_dc_v1",
        "amounts": [89.01, 8.40, -2025.0, -1000.0, -974.75, 83.61, 174.44],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        SISBR - SISTEMA DE INFORMÁTICA DO SICOOB EXTRATO CONTA CORRENTE
        COOP.: CONTA: DATA DOCUMENTO HISTÓRICO VALOR
        31/10/2018 SALDO ANTERIOR 21.922,83C
        31/10/2018 SALDO BLOQUEADO ANTERIOR 0,00*
        01/11/2018 88427995 CR COMPRAS VISA ELECTRON 89,01C
        01/11/2018 88427994 CR COMPRAS MAESTRO 8,40C
        01/11/2018 000.278 CHEQUE PAGO CAIXA 2.025,00D
        01/11/2018 000.279 CHEQUE PAGO CAIXA 1.000,00D
        SALDO DO DIA ===== > 18.995,24C
        05/11/2018 MASTERCARD DÉB.CONV.DEMAIS EMPRESAS 974,75D
        05/11/2018 88530859 CR COMPRAS VISA 83,61C
        05/11/2018 88530858 CR COMPRAS CRE OUTRAS BANDEIRAS 174,44C
        """,
    },
    "cartao_lancamentos_futuros": {
        "profile": "sicoob_cartao_lancamentos_futuros_v1",
        "amounts": [-19.83, -132.96],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        LANÇAMENTOS FUTUROS DO CARTÃO DE CRÉDITO
        COOP.: NOME: Nº CARTÃO: AUTORIZAÇÕES PENDENTES
        Não há autorizações pendentes.
        MOVIMENTOS PARA A PRÓXIMA FATURA Data Comércio Valor
        07/03 VIVO SC LJ C021 - 9/12 19,83
        26/08 MERCADOLIVRE*MERCADOL - 3/3 132,96
        """,
    },
    "poupanca_cooperada": {
        "profile": "sicoob_extrato_poupanca_cooperada_v1",
        "amounts": [0.24, 0.66, -0.20, 0.31, 1.03, -0.30],
        "balances": [338.62, 339.28, 339.08, 339.39, 340.42, 340.12],
        "text": """
        SICOOB - Sistema de Cooperativas de Crédito do Brasil
        Plataforma de Serviços Financeiros do Sicoob - SISBR Extrato Poupança Cooperada
        Agência: Conta: Data Documento Histórico Débito Crédito Saldo
        01/10/2022 SALDO ANTERIOR 338,38+
        11/10/2022 CORREÇÃO MONETÁRIA - SELIC 0,24+ 338,62+
        11/10/2022 JUROS - SELIC 0,66+ 339,28+
        11/10/2022 I.R.R.F APL. FIN - SELIC 0,20- 339,08+
        25/10/2022 CORREÇÃO MONETÁRIA - SELIC 0,31+ 339,39+
        25/10/2022 JUROS - SELIC 1,03+ 340,42+
        25/10/2022 I.R.R.F APL. FIN - SELIC 0,30- 340,12+
        """,
    },
    "aplicacoes_com_saldo": {
        "profile": "sicoob_extrato_aplicacoes_v1",
        "amounts": [180.80, -20014.25, -40.68],
        "balances": [480180.80, 460166.55, 460125.87],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        Plataforma de Serviços Financeiros do Sicoob - SISBR EXTRATO DE APLICAÇÕES
        DADOS DO CLIENTE DADOS DA APLICAÇÃO NÚMERO DA APLICAÇÃO VALOR INICIAL
        HISTÓRICO DE MOVIMENTAÇÃO DATA HISTÓRICO VALOR SALDO ATUAL
        09/03/2022 APLICAÇÃO FINANCEIRA 480.000,00
        11/04/2022 CAPITALIZAÇÃO DE CORREÇÃO MONETÁRIA 180,80 C 480.180,80
        11/04/2022 RESGATE DE APLICAÇÃO FINANCEIRA 20.014,25 D 460.166,55
        11/04/2022 RETENÇÃO DE IRRF 40,68 D 460.125,87
        """,
    },
    "apropriacao_diaria": {
        "profile": "sicoob_extrato_apropriacao_diaria_v1",
        "amounts": [0.01, 0.01, -5.02, -0.15, 0.01],
        "text": """
        - SICOOB - Sistema de Cooperativas de Crédito do Brasil
        Plataforma de Serviços Financeiros do Sicoob - SISBR
        Extrato de Apropriação Diária 01/08/2023 10:38:02
        MODALIDADE: RDC - LONGO PÓS CDI Nº APLICAÇÃO: DATA DA APLICAÇÃO:
        Data Histórico Valor
        30/06/2023 SALDO ANTERIOR R$ 7,49C
        04/07/2023 APROPRIAÇÃO DE CM R$ 0,01C
        06/07/2023 APROPRIAÇÃO DE CM R$ 0,01C
        27/07/2023 RESGATE DE APLICAÇÃO FINANCEIRA R$ 5,02D
        27/07/2023 RETENÇÃO DE IRRF R$ 0,15D
        28/07/2023 APROPRIAÇÃO DE CM R$ 0,01C
        RESUMO SALDO BRUTO EM 28/07/2023 R$ 2,40 SALDO DISPONÍVEL R$ 2,33
        """,
    },
    "pix_lista": {
        "profile": "sicoob_extrato_pix_lista_v1",
        "amounts": [117.0, 75.0, 78.0, 90.0, 360.0],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        01/04/2024 a 30/04/2024
        ABR 30 PIX RECEBIDO Recebimento Pix Igor ***.924.092-** + R$ 117,00
        ABR 30 PIX RECEBIDO Recebimento Pix Igor ***.924.092-** + R$ 75,00
        ABR 30 PIX RECEBIDO Recebimento Pix Carjane 136.882-** + R$ 78,00
        ABR 30 PIX RECEBIDO Recebimento Pix Jose ***.769.212-** + R$ 90,00
        ABR 30 PIX RECEBIDO Recebimento Pix MARCO ALMEIDA ***.324.772-** + R$ 360,00
        """,
    },
    "aplicacoes_sem_saldo": {
        "profile": "sicoob_extrato_aplicacoes_v1",
        "amounts": [40.61, -2574.81, -9.14, 66.25, -2452.38, -14.91],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        24/09/2024 EXTRATO DE APLICAÇÕES 11:50:58 CLIENTE CPF/CGC CONTA
        DADOS DA APLICAÇÃO MODALIDADE: RDC DI 30 DATA APLICAÇÃO ÍNDICE CM: CDI
        HISTÓRICO DE MOVIMENTAÇÃO DATA HISTÓRICO VALOR
        24/04 APLICAÇÃO FINANCEIRA
        20/06 CAPITALIZAÇÃO DE CORREÇÃO MONETÁRIA 40,61
        20/06 RESGATE DE APLICAÇÃO FINANCEIRA 2.574,81
        20/06 RETENÇÃO DE IRRF 9,14
        28/07 CAPITALIZAÇÃO DE CORREÇÃO MONETÁRIA 66,25
        28/07 RESGATE DE APLICAÇÃO FINANCEIRA 2.452,38
        28/07 RETENÇÃO DE IRRF 14,91
        """,
    },
    "conta_corrente_moderna": {
        "profile": "sicoob_extrato_conta_corrente_moderno_v1",
        "amounts": [-5.70, 2083.0, -10.0, -3.80],
        "text": """
        SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
        PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
        EXTRATO DE CONTA CORRENTE Cooperativa Conta Período 01/12/2024 - 31/12/2024
        HISTÓRICO DE MOVIMENTAÇÃO Data Documento Histórico Valor
        31/12 431875 TARIFA COBRANÇA R$ 5,70
        31/12 431429 CRÉD.LIQUIDAÇÃO COBRANÇA R$ 2.083,00
        SALDO DO DIA R$ 2.102,39
        26/12 430861 TARIFA COBRANÇA R$ 10,00
        SALDO DO DIA R$ 25,09
        23/12 430168 TARIFA COBRANÇA R$ 3,80
        """,
    },
    "comprovante_boleto": {
        "profile": "sicoob_comprovante_pagamento_boleto_v1",
        "amounts": [-3365.66],
        "text": """
        SICOOB - Sistema de Cooperativas de Crédito do Brasil
        Plataforma de Serviços Financeiros do Sicoob - SISBR
        Comprovante de Pagamento de Boleto Data: 17/02/2025 Coop. Conta
        Linha digitável: 34191.09008 20509.406284 52916.100002 9 995
        Nº documento: 975221 Nosso Número: 62852916 Instituição Emissora: 341-ITAU UNIBANCO S.A.
        Nome/Razão Social do Beneficiário: SUPLE Nome Fantasia Beneficiário: SUPLE
        Data Agendamento: 29/12/2024-22:13:47 Data Pagamento: 06/01/2025
        Data Vencimento: 04/01/2025 Valor Documento: 3.365,66
        (-) Desconto / Abatimento: 0,00 (+) Outros acréscimos: 0,00
        Valor Pago: 3.365,66 Situação: Efetivado Autenticação: 0804b006
        """,
    },
    "creditran_detalhado": {
        "profile": "sicoob_creditran_extrato_detalhado_conta_v1",
        "amounts": [-45.41, -3.75, -80.76, -179.20, 224.0, -47.93],
        "balances": [-3362.60, -3366.35, -3447.11, -3626.31, -3402.31, -3450.24],
        "text": """
        SICOOB Creditran Conta Corrente 06/02/2025 15:46:18
        Banco Agência Conta Corrente EXTRATO DETALHADO CONTA
        PERÍODO DE 01/01/2025 A 31/01/2025 Últimos Lançamentos Saldo anterior -3.317,19
        Data Histórico Documento Valor Saldo
        31/12/2024 DÉB.SEGURO EMPRÉSTIMO 0009731610 -45,41 -3.362,60
        31/12/2024 JUROS ADIANT.DEPOSITANTE AD/31-12 -3,75 -3.366,35
        31/12/2024 JUROS CONTA GARANTIDA LC-202411 -80,76 -3.447,11
        31/12/2024 MANUTENÇÃO SOFTWARE 11/2024 -179,20 -3.626,31
        31/12/2024 OUTROS CRÉDITOS 11/2024 224,00 -3.402,31
        02/01/2025 DÉB.SEGURO EMPRÉSTIMO 0009974607 -47,93 -3.450,24
        """,
    },
}


@pytest.mark.parametrize("case_name", SICOOB_EXAMPLE_CASES)
def test_parse_pdf_page_texts_supports_sicoob_visual_examples(case_name: str) -> None:
    case = SICOOB_EXAMPLE_CASES[case_name]
    # Text-only layout examples have no PDF geometry; native integration tests
    # use real containers and verify detached signs and page continuations.
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [case["text"]], context=LayoutSpecificParseContext(is_native_text=True))

    assert result.layout.layout_name == case["profile"]
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    if "balances" in case:
        assert [transaction.running_balance for transaction in result.canonical_transactions] == case["balances"]
    ofx = build_ofx_statement(
        result.transactions,
        account_type="credit_card" if case_name in {"fatura_cartao", "cartao_lancamentos_futuros"} else None,
    )
    assert ofx.count("<STMTTRN>") == len(case["amounts"])


@pytest.mark.parametrize("case_name", ["fatura_cartao", "cartao_lancamentos_futuros"])
def test_sicoob_card_layouts_resolve_credit_card_ofx_account_type(case_name: str) -> None:
    case = SICOOB_EXAMPLE_CASES[case_name]

    account_type = _resolve_ofx_account_type(
        extension="pdf",
        filename="sicoob.pdf",
        raw_bytes=b"",
        extracted_text=case["text"],
        layout_inference_name=case["profile"],
    )

    assert account_type == "credit_card"


def test_parse_sicoob_monospace_textract_rows_preserves_dates_and_stops_before_summary(
    monkeypatch,
) -> None:
    pages = [
        """
        SICOOB - Sistema de Cooperativas de Crédito do Brasil
        SISBR - SISTEMA DE INFORMÁTICA DO SICOOB
        EXTRATO CONTA CORRENTE
        COOP.
        CONTA
        DATA
        DOCUMENTO
        HISTÓRICO
        VALOR
        30/06/2026
        SALDO ANTERIOR
        1.006,20C
        01/07/2026 000001
        CR COMPRAS MAESTRO
        500,00C
        01/07/2026 000002
        PIX ENVIADO - OUTRA IF
        50,00D
        SALDO DO DIA
        1.456,20C
        02/07/2026 000003
        TARIFA BANCÁRIA
        6,20D
        1/2
        """,
        """
        28/09/2026, 08:34
        SICOOB
        EXTRATO CONTA CORRENTE
        DATA
        DOCUMENTO
        HISTÓRICO
        VALOR
        SALDO DO DIA
        >
        1.450,00C
        03/07/2026 000004 CR COMPRAS MAESTRO
        100,000
        SALDO DO DIA
        1.550,00C
        (+) SALDO EM CONTA:
        1.550,00C
        VENCIMENTO DO LIMITE CONTRATADO:
        15/12/2026
        TAXA DE JUROS MENSAL (A.M.):
        8,00%
        TAXA DE JUROS MORATÓRIOS (A.M.):
        0,00%
        """,
    ]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: pages)
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: pages)

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "sicoob_sisbr_extrato_conta_corrente_monospace_valor_dc_v1"
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert [(transaction.date, transaction.amount, transaction.running_balance) for transaction in result.canonical_transactions] == [
        ("2026-07-01", 500.0, None),
        ("2026-07-01", -50.0, 1456.2),
        ("2026-07-02", -6.2, 1450.0),
        ("2026-07-03", 100.0, 1550.0),
    ]
