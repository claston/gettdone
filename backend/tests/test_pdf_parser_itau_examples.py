import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement

ITAU_EXAMPLE_CASES = {
    "saldo_resumido": {
        "profile": "itau_empresas_extrato_lancamentos_conta_corrente_v1",
        "amounts": [-109500.0],
        "text": """
        Itaú Empresas
        Saldo resumido
        saldo em conta corrente 13.043,86
        Extrato conta corrente / Lançamentos
        período: 01/07/2021 até 31/07/2021
        data lançamentos ag/origem valor (R$) saldo (R$)
        01 / jul SALDO INICIAL 10,00
        01 / jul TRANSF TITUL TED 4015 -109.500,00
        """,
    },
    "conta_corrente_aplicacoes": {
        "profile": "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1",
        "amounts": [-3000.0, -165.0, -201.9, 3029.28, 0.02],
        "text": """
        ItaúEmpresas
        extrato mensal
        01. Conta Corrente e Aplicações Automáticas
        Conta Corrente | Movimentação
        data descrição entradas R$ (créditos) saídas R$ (débitos) saldo R$
        saldo em 27/10/2021 R$ 3.039,28
        saldo em 30/11/2021 R$ 2.445,56
        27/10 Saldo anterior 3.039,28
        03/11 Sispag 3.000,00-
        03/11 Tar Contr 165,00-
        03/11 Tar Conta Certa 201,90-
        03/11 Res Aplic Aut Mais 3.029,28
        03/11 Rend Pag Aplic Aut Mais 0,02
        """,
    },
    "trinta_horas": {
        "profile": "itau_empresas_extrato_30_horas_tabela_v1",
        "amounts": [-1565.2, -1700.0, -4939.33, -40.1],
        "text": """
        ItaúEmpresas
        30 horas
        Nome Agência/Conta Data Horário
        Extrato de 01/10/2021 até 31/10/2021
        Data Lançamento Ag./Origem Valor (R$) Saldo (R$)
        30/09 SALDO ANTERIOR 10,00
        01/10 SISPAG FORNECEDORES 3796 -1.565,20
        01/10 SISPAG FORNECEDORES TED 3796 -1.700,00
        01/10 D SISPAG FORNECEDORES 3796 -4.939,33
        01/10 TAR/CUSTAS COBRANCA -40,10
        """,
    },
    "historico_lancamentos": {
        "profile": "itau_extrato_historico_lancamentos_orig_valor_saldo_v1",
        "amounts": [-206.66, -211.89, -239.08, -135.05, -58.93, -236.4, -67.64, -300.27, -319.98],
        "text": """
        Itaú Agência Conta Nome JANEIRO/2022
        Data Histórico de Lançamentos Orig Valor (R$) Saldo (R$)
        03/01 SALDO INICIAL 10,00
        03/01 SISPAG BOLETO 1380 206,66-
        03/01 SISPAG BOLETO 1380 211,89-
        03/01 SISPAG BOLETO 1380 239,08-
        03/01 SISPAG CONCESSIONARIA 1380 135,05-
        03/01 SISPAG CONCESSIONARIA 1380 58,93-
        03/01 SISPAG BOLET OUTR BCO 1380 236,40-
        03/01 SISPAG BOLET OUTR BCO 1380 67,64-
        03/01 SISPAG BOLET OUTR BCO 1380 300,27-
        03/01 SISPAG BOLET OUTR BCO 1380 319,98-
        """,
    },
    "extrato_completo_cards": {
        "profile": "itau_empresas_extrato_completo_cards_v1",
        "amounts": [-226.7],
        "text": """
        ItaúEmpresas dados gerais
        nome agência/conta data horário
        consolidado
        31/03/2022 saldo do dia R$ 10,00
        30/03/2022 SEGURO ITAUEMPRESA 11/11 - R$ 226,70
        """,
    },
    "posicao_conta_corrente": {
        "profile": "itau_empresas_extrato_30_horas_posicao_conta_corrente_v1",
        "amounts": [2436.5, -122.0, -520.0, -113.99],
        "text": """
        Banco Itaú S/A ItaúEmpresas 30 horas
        Extrato de conta corrente
        Posição da Conta Corrente
        01/10/2022 a 31/10/2022
        Data Lançamento Valor (R$) Saldo (R$)
        03/10 SALDO ANTERIOR 10,00
        04/10 PIX 9773 2.436,50
        04/10 TAR 6381 122,00 -
        04/10 SDO 7.593,74
        05/10 SISPAG 6381 520,00-
        05/10 VIVO 6381 113,99-
        """,
    },
    "cobranca_movimentacao": {
        "profile": "itau_empresas_cobranca_movimentacao_detalhada_v1",
        "amounts": [1049.06, 1049.05, 652.59, 652.59, 652.59],
        "text": """
        Itaú 30 horas Resultado da Busca
        Movimentação resumida de Cobrança
        Data de movimentação: 14/08/2024 Tipo de consulta: Detalhada
        Movimentação detalhada
        Cart Nosso Número Seu Número Nome do Pagador Vcto. Agência Dep Rec Tipo Movimentação Valor(R$)
        109 005091088 NF 14861 CLIENTE UM 16/09/2024 0054 BOLETO E 14/08/2024 1.049,06
        109 005091096 NF 14861 CLIENTE UM 16/09/2024 0054 BOLETO E 14/08/2024 1.049,05
        109 005091104 NF 14862 CLIENTE DOIS 16/09/2024 0028 BOLETO E 14/08/2024 652,59
        109 005091112 NF 14862 CLIENTE DOIS 16/09/2024 0028 BOLETO E 14/08/2024 652,59
        109 005091120 NF 14862 CLIENTE DOIS 23/09/2024 0028 BOLETO E 14/08/2024 652,59
        """,
    },
    "consulta_pagamentos": {
        "profile": "itau_empresas_consulta_pagamentos_transferencias_pix_v1",
        "amounts": [-100.0, -2000.0, -816.73, -3996.0, -500.0, -405.2],
        "text": """
        Itaú 30 horas
        consulta de pagamentos, transferências e Pix
        período: 01/06/2024 a 30/06/2024 status: todos tipo de pagamento: todos
        favorecido/beneficiário CPF/CNPJ tipo de pagamento referência da empresa data do pagamento valor (R$) status
        BENEFICIARIO UM ***.111.111-** PIX Transferências - 28/06/2024 100,00 efetuado
        BENEFICIARIO DOIS ***.222.222-** PIX Transferências - 28/06/2024 2.000,00 efetuado
        BENEFICIARIO TRES ***.333.333-** PIX Transferências - 27/06/2024 816,73 efetuado
        BENEFICIARIO QUATRO ***.444.444-** PIX Transferências - 27/06/2024 3.996,00 efetuado
        BENEFICIARIO CINCO PIX Transferências - 27/06/2024 500,00 efetuado
        BENEFICIARIO SEIS PIX Transferências - 26/06/2024 405,20 efetuado
        """,
    },
    "poupanca": {
        "profile": "itau_extrato_poupanca_entradas_saidas_v1",
        "amounts": [-0.17, 0.11, 0.68, 100.0, 100.0, 300.0],
        "text": """
        Itaú extrato de poupança jan 2025
        Minha conta Minha agência
        data descrição agência/origem rentabilidade % entradas R$ saídas R$ saldo R$
        02/12 Saldo anterior 3.571,95
        02/01 Imposto Renda 0,00 0,17-
        02/01 Remuner Básica 0,0822 0,11 0,00
        02/01 Juros 0,5000 0,68 0,00
        02/01 PIX 992 100,00 0,00
        02/01 PIX TRANSF 902 100,00 0,00
        02/01 PIX TRANSF 977 300,00 0,00
        """,
    },
    "transferencias_recebidas": {
        "profile": "itau_empresas_transferencias_recebidas_v1",
        "amounts": [13832.27, 272.75, 6636.48, 1287.19, 3625.51, 438.0, 1891.85],
        "text": """
        ItaúEmpresas agência conta corrente
        transferências recebidas emitida em 05/09/2025
        pagador cpf/cnpj id de pagamento recebido em valor (R$)
        EMPRESA UM 50.156.978/0001-15 05/09/2025 13.832,27
        PESSOA DOIS 073.716.728-95 05/09/2025 272,75
        EMPRESA TRES 12.210.875/0001-05 05/09/2025 6.636,48
        EMPRESA QUATRO 06.916.343/0001-87 05/09/2025 1.287,19
        EMPRESA CINCO 03.974.038/0001-53 04/09/2025 3.625,51
        EMPRESA SEIS 18.918.152/0001-33 04/09/2025 438,00
        EMPRESA SETE 07.719.000/0001-95 04/09/2025 1.891,85
        """,
    },
    "extrato_completo_tabela": {
        "profile": "itau_empresas_extrato_completo_tabela_v1",
        "amounts": [419.69, 43.96, 1250.37, 927.43],
        "text": """
        Itaú
        Agência Conta Saldo total Limite da conta Utilizado Disponível
        Lançamentos do período: 01/08/2025 até 31/08/2025
        Data Lançamentos Razão Social CNPJ/CPF Valor (R$) Saldo (R$)
        31/07/2025 SALDO ANTERIOR 14.600,37
        01/08/2025 REDE MAST REDECARD INSTITUICAO DE PAGAMENTO S.A. 419,69
        01/08/2025 REDE ELO REDECARD INSTITUICAO DE PAGAMENTO S.A. 43,96
        01/08/2025 REDE VISA REDECARD INSTITUICAO DE PAGAMENTO S.A. 1.250,37
        01/08/2025 REDE MAST REDECARD INSTITUICAO DE PAGAMENTO S.A. 927,43
        01/08/2025 SALDO TOTAL DISPONÍVEL 16.241,82
        """,
    },
    "comprovante_transferencia": {
        "profile": "itau_comprovante_transferencia_pix_v1",
        "amounts": [-4000.0],
        "text": """
        Itaú 30 horas Comprovante de Transferência
        dados do pagador nome do pagador CPF / CNPJ do pagador agência/conta
        dados do recebedor nome do recebedor chave CPF / CNPJ do recebedor instituição
        dados da transação
        valor: R$ 4.000,00
        data da transferência: 01/08/2025
        tipo de pagamento: PIX TRANSFERENCIA
        identificação no comprovante autenticação no comprovante
        """,
    },
}


@pytest.mark.parametrize("case_name", ITAU_EXAMPLE_CASES)
def test_parse_pdf_transactions_supports_itau_visual_examples(case_name: str, monkeypatch) -> None:
    case = ITAU_EXAMPLE_CASES[case_name]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [case["text"]])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [case["text"]])

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == case["profile"]
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    ofx = build_ofx_statement(result.transactions)
    assert ofx.count("<STMTTRN>") == len(case["amounts"])
    assert all(f"<TRNAMT>{amount:.2f}" in ofx for amount in case["amounts"])
