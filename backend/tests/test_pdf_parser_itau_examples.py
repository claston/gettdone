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


def test_itau_monthly_keeps_date_across_pages_and_stops_before_investment_details() -> None:
    pages = [
        """
        ItaúEmpresas
        extrato mensal
        out 2025
        01. Conta Corrente e Aplicações Automáticas
        Conta Corrente | Movimentação
        data descrição entradas R$ saídas R$ saldo R$
        30/09
        Saldo anterior
        1,00
        01/10
        PIX TRANSF CLIENTE01/10
        100,00
        Apl Aplic Aut Mais
        100,00-
        1,00
        SALDO APLIC AUT MAIS
        101,00
        02/10
        Sispag Fornecedores
        10,00-
        """,
        """
        extrato
        mensal
        ag 0624 cc 20958-1
        out 2025
        002|003
        data
        descrição
        entradas R$
        saídas R$
        saldo R$
        (créditos)
        (débitos)
        Sispag Fornecedores
        20,00-
        Res Aplic Aut Mais
        30,00
        1,00
        SALDO APLIC AUT MAIS
        71,00
        03/10
        PIX TRANSF CLIENTE03/10
        50,00
        Apl Aplic Aut Mais
        50,00-
        1,00
        SALDO APLIC AUT MAIS
        121,00
        Saldo em C/C
        1,00
        Saldo final
        122,00
        totalizador de aplicações automáticas
        entrada R$
        saída R$
        (créditos)
        (débitos)
        na conta corrente (1)
        30,00
        150,00-
        Conta Corrente | Aplicações Automáticas
        movimentação - aplicações/resgates antecipados e vencimentos
        01/10
        100,00
        0,00
        0,00
        0,00
        0,00
        0,00
        101,00
        total
        150,00
        30,00
        0,00
        0,00
        0,00
        0,00
        """,
        """
        extrato mensal
        Conta Corrente | Saques efetuados
        total R$ 500,00
        data histórico valor R$
        02/10/25
        SAQUE DIN ATM
        500,00
        """,
    ]

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)

    assert result.layout.layout_name == (
        "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1"
    )
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert [(transaction.date, transaction.amount) for transaction in result.transactions] == [
        ("2025-10-01", 1.0),
        ("2025-10-01", 100.0),
        ("2025-10-01", -100.0),
        ("2025-10-02", -10.0),
        ("2025-10-02", -20.0),
        ("2025-10-02", 30.0),
        ("2025-10-03", 50.0),
        ("2025-10-03", -50.0),
    ]
    assert [transaction.description for transaction in result.transactions[3:6]] == [
        "Sispag Fornecedores",
        "Sispag Fornecedores",
        "Res Aplic Aut Mais",
    ]


def test_itau_monthly_handles_sidebar_before_investment_balance_and_split_negative_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda _text: pdf_parser_module.PdfLayoutInference(
            layout_name="itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1",
            confidence=1.0,
            used_fallback=False,
        ),
    )
    text = """
    ItaÃºEmpresas
    extrato mensal
    set 2026
    01. Conta Corrente e AplicaÃ§Ãµes AutomÃ¡ticas
    saldo em 31/08/26
    saldo em 30/09/26
    total entradas
    total saÃ­das
    Conta Corrente | MovimentaÃ§Ã£o
    data
    descriÃ§Ã£o
    entradas R$
    saÃ­das R$
    saldo R$
    (crÃ©ditos)
    (dÃ©bitos)
    31/08
    Saldo anterior
    636,90
    01/09
    PIX ENVIADO CLIENTE
    600,00-
    G = aplicaÃ§Ã£o programada
    Res Aplic Aut Mais
    600,00
    1,00
    P = poupanÃ§a automÃ¡tica
    SALDO APLIC AUT MAIS
    35,90
    Para demais siglas, consulte as Notas
    Explicativas no final do extrato
    02/09
    IOF
    7,76-
    Tar Plano Adapt
    98,99-
    TAR PIX QR LIQ ESTATICO
    9,10-
    Res Aplic Aut Mais
    35,90
    78,95
    -
    03/09
    PIX TRANSF CLIENTE
    100,00
    Apl Aplic Aut Mais
    20,05-
    1,00
    SALDO APLIC AUT MAIS
    20,05
    Saldo em C/C
    1,00
    Saldo final
    21,05
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == (
        "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1"
    )
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert [transaction.description for transaction in result.transactions] == [
        "SALDO ANTERIOR",
        "PIX ENVIADO CLIENTE",
        "Res Aplic Aut Mais",
        "IOF",
        "Tar Plano Adapt",
        "TAR PIX QR LIQ ESTATICO",
        "Res Aplic Aut Mais",
        "PIX TRANSF CLIENTE",
        "Apl Aplic Aut Mais",
    ]
    assert result.canonical_transactions is not None
    assert result.canonical_transactions[6].running_balance == -78.95
    assert result.canonical_transactions[8].running_balance == 1.0


def test_itau_monthly_preserves_explicit_sispag_salary_debit_sign() -> None:
    text = """
    ItaúEmpresas
    extrato mensal
    ago 2026
    01. Conta Corrente e Aplicações Automáticas
    saldo em 10/08/2026 R$ 1,00
    saldo em 11/08/2026 R$ 100.001,00
    Conta Corrente | Movimentação
    data descrição entradas R$ (créditos) saídas R$ (débitos) saldo R$
    10/08
    Saldo anterior
    1,00
    11/08
    Sispag Salários
    900,00-
    Res Aplic Aut Mais
    900,00
    1,00
    SALDO APLIC AUT MAIS
    100.000,00
    Saldo final
    100.001,00
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == (
        "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1"
    )
    assert [(transaction.description, transaction.amount) for transaction in result.transactions] == [
        ("SALDO ANTERIOR", 1.0),
        ("Sispag Salários", -900.0),
        ("Res Aplic Aut Mais", 900.0),
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_itau_complete_table_attaches_vertical_daily_balances_without_importing_them() -> None:
    text = """
    Itaú
    Agência Conta Saldo total Limite da conta Utilizado Disponível
    Lançamentos do período: 17/12/2025 até 19/12/2025
    Data Lançamentos Razão Social CNPJ/CPF Valor (R$) Saldo (R$)
    16/12/2025 SALDO ANTERIOR -4.000,00
    17/12/2025
    PIX RECEBIDO
    100,00
    17/12/2025
    SALDO TOTAL DISPONÍVEL DIA
    -3.900,00
    RECEBIMENTOS GUIMARAES
    18/12/2025
    DEB AUTOR SEGURO
    -375,11
    18/12/2025
    SALDO TOTAL DISPONÍVEL DIA
    -4.275,11
    COMPRA NO DÉBITO
    19/12/2025
    PIX ENVIADO
    -442,35
    19/12/2025
    SALDO TOTAL DISPONÍVEL DIA
    -4.717,46
    """

    profile = pdf_parser_module.get_layout_profile("itau_empresas_extrato_completo_tabela_v1")
    assert profile is not None
    lines = [
        pdf_parser_module._PdfLine(text=value.strip(), page_number=1, line_number=index)
        for index, value in enumerate(text.splitlines(), start=1)
        if value.strip()
    ]

    rows, candidates = pdf_parser_module._parse_tabular_statement_rows(lines, layout_profile=profile)

    assert candidates == 4  # Three transactions plus the skipped opening-balance candidate.
    assert [
        (row.transaction.date, row.transaction.description, row.transaction.amount)
        for row in rows
    ] == [
        ("2025-12-17", "PIX RECEBIDO", 100.0),
        ("2025-12-18", "DEB AUTOR SEGURO", -375.11),
        ("2025-12-19", "PIX ENVIADO", -442.35),
    ]
    assert [row.running_balance for row in rows] == [
        -3900.0,
        -4275.11,
        -4717.46,
    ]
