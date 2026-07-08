from app.application.pdf_layout_inference import infer_pdf_layout


def test_infer_pdf_layout_prefers_nubank_profile_when_tokens_match() -> None:
    text = """
    06 NOV 2023 Total de entradas + 1.069,04
    Transferencia recebida pelo Pix BANCO TESTE
    Saldo do dia 4.583,57
    Total de saidas - 4.000,00
    Transferencia enviada pelo Pix FULANO
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "nubank_statement_ptbr"
    assert result.confidence >= 0.6


def test_infer_pdf_layout_prefers_nubank_profile_when_brand_token_matches() -> None:
    text = """
    Nubank
    Conta
    15/04/2026 PIX RECEBIDO CLIENTE 100,00 1.100,00
    16/04/2026 PAGAMENTO BOLETO -50,00 1.050,00
    Saldo disponivel
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "nubank_statement_ptbr"
    assert result.confidence >= 0.7


def test_infer_pdf_layout_prefers_itau_profile_when_tokens_match() -> None:
    text = """
    saldo em conta Limite da Conta utilizado Limite da Conta disponível
    extrato conta / lançamentos
    data lançamentos valor (R$) saldo (R$)
    13/04/2026 PIX TRANSF ERICA -2.835,00
    13/04/2026 SALDO DO DIA -4.142,48
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "itau_statement_ptbr"
    assert result.confidence >= 0.6


def test_infer_pdf_layout_prefers_itau_empresas_30_horas_posicao_profile() -> None:
    text = """
    Banco Itau S/A
    ItauEmpresas
    30 horas
    Extrato de conta corrente
    Nome:
    Agencia:
    Conta:
    Posicao da Conta Corrente
    01/10/2022 a 31/10/2022
    Data Lancamento Valor (R$) Saldo (R$)
    03/10 SALDO ANTERIOR 10,00
    04/10 PIX 9773 2.436,50
    04/10 TAR 6381 122,00-
    04/10 SDO 7.593,74
    05/10 SISPAG 6381 520,00-
    05/10 VIVO 6381 113,99-
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "itau_empresas_extrato_30_horas_posicao_conta_corrente_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_itau_historico_lancamentos_orig_profile() -> None:
    text = """
    Itau
    Agencia:
    Conta:
    Nome:
    JANEIRO/2022
    Data Historico de Lancamentos Orig Valor (R$) Saldo (R$)
    03/01 SALDO INICIAL 10,00
    03/01 SISPAG BOLETO 1380 206,66-
    03/01 SISPAG BOLETO 1380 211,89-
    03/01 SISPAG CONCESSIONARIA 1380 135,05-
    03/01 SISPAG BOLET OUTR BCO 1380 236,40-
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "itau_extrato_historico_lancamentos_orig_valor_saldo_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_falls_back_to_generic_profile() -> None:
    text = """
    01 JAN 2026
    PAGAMENTO FORNECEDOR ALFA
    980,00
    02 JAN 2026
    RECEBIMENTO CLIENTE BRAVO
    1500,00
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "generic_statement_ptbr"
    assert result.confidence >= 0.2


def test_infer_pdf_layout_prefers_santander_profile_when_tokens_match() -> None:
    text = """
    BANCO SANTANDER BRASIL S.A.
    EXTRATO DE CONTA CORRENTE
    agencia: 1234 conta: 00012345-6
    data historico documento valor (R$) saldo (R$)
    13/04/2026 PIX RECEBIDO CLIENTE 1.250,00 4.142,48
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_statement_ptbr"
    assert result.confidence >= 0.55


def test_infer_pdf_layout_prefers_santander_negocios_profile_with_credit_debit_table() -> None:
    text = """
    Extrato Santander Negócios & Empresas - Saldo Coerente
    Santander Negócios & Empresas
    Resumo - março/2021
    Conta Corrente
    Movimentação
    Data Descrição Nº Documento Créditos Débitos Saldo
    SALDO EM 28/02 0,00
    01/03 TARIFA RECOLHIMENTO DE VALORES - 257,62- -257,62
    02/03 PIX ENVIADO FORNECEDOR XYZ 991112 1.500,00- 4.629,72
    03/03 PIX RECEBIDO CLIENTE ALFA 102551 2.850,00 7.479,72
    04/03 PAGAMENTO BOLETO 881211 980,00- 6.499,72
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_negocios_empresas_extrato_consolidado_inteligente_conta_corrente_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_new_santander_empresarial_grouped_period_profile() -> None:
    text = """
    Santander
    Internet Banking Empresarial
    Agencia:
    Conta:
    Banco Santander Pessoa Juridica
    Busque por um periodo
    Periodo
    01/02/2022 - 28/02/2022
    Exibindo resultados para 01/02/2022 a 28/02/2022
    Para consultas acima de 90 dias clique aqui.
    Todos
    Creditos
    Debitos
    Quarta, 02 de fevereiro de 2022
    PIX ENVIADO OUTRA
    DEBITO
    -R$ 8.000,00
    TED RECEBIDA DIF TITULARIDADE STR
    CREDITO
    R$ 9.325,90
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_internet_banking_empresarial_periodo_agrupado_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_santander_empresarial_a4_date_history_value_profile() -> None:
    text = """
    Santander
    Internet Banking Empresarial
    Agencia:
    Conta:
    Data Historico Valor
    26/05/2025 Saldo do dia Cc R$ 0,00
    26/05/2025 Resgate R$ 737,67
    26/05/2025 Pix Enviado - R$ 737,67
    21/05/2025 Saldo do dia Cc R$ 0,00
    21/05/2025 Pix Recebido 04827261 R$ 178,90
    21/05/2025 Pagamento De Boleto Outros Bancos - R$ 759,44
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_internet_banking_empresarial_movimentacao_a4_data_historico_valor_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_santander_simples_conferencia_profile() -> None:
    text = """
    Data: Hora: Local:
    Santander
    Extrato de Conta para Simples Conferencia - USO INTERNO
    Mes Referencia: 05/2022
    Tipo Consolidacao :
    Conta :
    Cliente :
    Dt. Contabil Historico Descricao historico N documento Valor (R$) Saldo (R$)
    SALDO INICIAL 0,00
    06/05 398 PIX RECEBIDO OUTRA INST -MESMA TIT 10.000,00 10.000,00
    22948445
    398 PIX ENVIADO OUTRA INST - DIF TIT -1.468,90 8.531,10
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_simples_conferencia_extrato_conta_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_santander_monthly_landscape_consolidated_profile() -> None:
    text = """
    Santander
    UYLS0001
    EXTRATO MENSAL CONSOLIDADO - MES REFERENCIA:  01/2021
    CPF/CNPJ:
    MOVIMENTACAO CONTA CORRENTE
    ------------------------
    DATA
    DESCRICAO
    N.DOC
    MOVIMENTO(R$)
    31/12
    SALDO ANTERIOR
    0,00
    06/01
    COMPRA CARTAO MAESTRO
    46407
    12,46-
    06/01
    06/01
    COMPRA CARTAO MAESTRO
    48357
    4,00-
    06/01
    06/01
    RESGATE AUT CONTAMAX
    00000
    16,46
    06/01
    SALDO FINAL DIA
    0,00
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_extrato_mensal_consolidado_paisagem_conta_corrente_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_santander_vangogh_resumo_profile() -> None:
    text = """
    Santander Van Gogh EXTRATO CONSOLIDADO
    Resumo - agosto/2024
    Nome
    Agencia
    Conta Corrente
    (-) Saldo de Conta Corrente em 31/07 2.071,46
    (=) Saldo de Conta Corrente em 31/08 4.174,56
    (=) Saldo Disponivel de Conta Corrente 4.174,56
    Conta Corrente
    Movimentacao
    Data Descricao N Documento Movimento (R$) Saldo (R$)
    01/08 SALDO EM 31/07 2.071,46
    01/08 PIX RECEBIDO - 8.736,70
    RG FAMILY OFFICE ASSESSOR
    PAGAMENTO DE BOLETO OUTROS - 3.301,28-
    IOF IMPOSTO OF 3070/24 1,27-
    IOF ADICIONAL - AUTOMATICO - 14,81- 7.490,80
    06/08 PAGAMENTO DE BOLETO - 3.123,64-
    REMUNERACAO APLICACAO - 0,02 4.367,18
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_vangogh_resumo_consolidado_conta_corrente_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_santander_credit_card_invoice_detail_profile() -> None:
    text = """
    Data: 05/01/2026
    Detalhamento da Fatura
    Pagamento e Demais Creditos
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    02/01
    PAGAMENTO DE FATURA-INTERNET
    -20.000,00
    Parcelamentos
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    )))
    13/11
    .
    MEDICAMENTOS
    03/04
    852,50
    Despesas
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    )))
    23/12
    SUPERMERCADO
    112,76
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "santander_cartao_credito_detalhamento_fatura_paisagem_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_bradesco_profile_when_tokens_match() -> None:
    text = """
    BANCO BRADESCO S.A.
    EXTRATO MENSAL
    agencia 1234 conta 12345-6
    data historico valor saldo
    12/04/2026 TRANSFERENCIA PIX -250,00 2.845,10
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "bradesco_statement_ptbr"
    assert result.confidence >= 0.5


def test_infer_pdf_layout_prefers_bradesco_unificado_poupanca_profile() -> None:
    text = """
    Bradesco
    Extrato Unificado - Pessoa Juridica
    Invest Facil Bradesco
    Poupanca Facil
    Demonstrativo de Saldos e Rendimentos - Depositos a partir de 04/05/2012
    Em 30/11/2023
    Demonstrativo da Movimentacao
    Data
    Historico
    Documento
    Indices
    Credito
    Debito
    Saldo
    30/10
    Saldo Anterior
    8.847,52
    06/11
    Rendimentos
    0406058
    9,02
    Poup Facil-depos A Partir 4/5/12
    8.856,54
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_banco_do_nordeste_extrato_consolidado_profile() -> None:
    text = """
    Banco do
    Nordeste
    EXTRATO CONSOLIDADO
    Informacoes Gerais
    Titular:
    Mes:
    Marco/2023
    Data de Emissao:
    Detalhamento do Extrato
    REFERENCIA: MARCO/2023
    < RESUMO DAS MOVIMENTACOES NO PERIODO >
    > CONTA CORRENTE
    > DEMONSTRATIVO DA MOVIMENTACAO DE CONTA CORRENTE
    DIA
    HISTORICO
    DOCUMENTO
    VALOR
    SALDO
    1
    SALDO ANTERIOR
    0,00
    69,86
    1
    TARIFA MANUTENCAO CONTA
    474
    53,00-
    16,86
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "banco_do_nordeste_extrato_consolidado_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_bb_profile_when_tokens_match() -> None:
    text = """
    BANCO DO BRASIL
    EXTRATO CONTA CORRENTE
    agencia 1234-5 conta 98765-4
    data lancamentos documento valor saldo
    14/04/2026 PIX RECEBIDO CLIENTE 980,00 8.420,10
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "bb_statement_ptbr"
    assert result.confidence >= 0.5


def test_infer_pdf_layout_prefers_generic_when_specific_signal_is_weak() -> None:
    text = """
    BANCO SANTANDER
    RESUMO FINANCEIRO
    01 JAN 2026
    PAGAMENTO FORNECEDOR ALFA
    980,00
    02 JAN 2026
    RECEBIMENTO CLIENTE BRAVO
    1500,00
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "generic_statement_ptbr"
    assert result.used_fallback is True


def test_infer_pdf_layout_prefers_caixa_profile_when_tokens_match() -> None:
    text = """
    CAIXA ECONOMICA FEDERAL
    EXTRATO DA CONTA CORRENTE
    agencia: 1234 operacao: 001 conta: 12345-6
    data historico documento valor saldo
    15/04/2026 PIX RECEBIDO CLIENTE 1.150,00 6.421,34
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_statement_ptbr"
    assert result.confidence >= 0.5


def test_infer_pdf_layout_prefers_caixa_landscape_datetime_detail_profile_for_real_extracted_text() -> None:
    text = """
    Cliente:
    Conta:
    Data: 08/09/2025
    Saldo proprio
    R$ 874,95 C
    Saldo bloqueado
    R$ 0,00 C
    Limite contratado
    R$ 1.000,00 C
    Saldo
    R$ 3.978,14 C
    4 de agosto de 2025, segunda-feira
    Data/Hora
    Nr. Doc.
    Descricao/Detalhamento
    Valor (R$)
    Saldo(R$)
    02/08/2025
    03:32:14
    310725
    COB INTERN
    582,18 C
    522,18 C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_extrato_paisagem_data_hora_detalhamento_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_caixa_gerenciador_period_effective_date_profile() -> None:
    text = """
    GERENCIADOR
    C
    A
    I
    X
    A
    CNPJ:
    Agencia:
    Conta:
    02/12/2025 11:22:20
    Saldo anterior ao periodo solicitado
    R$ 44.826,29 C
    Extrato no periodo de 01/11/2025 a 30/11/2025
    Data
    Data Efetiva
    Documento
    Historico
    Valor
    Saldo
    03/11/2025
    01/11 22:19
    012219
    CRED PIX QR
    R$ 15,20
    R$ 44.841,49 C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_gerenciador_extrato_periodo_data_efetiva_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_caixa_sihex_historico_extratos_profile() -> None:
    text = """
    CAI
    X
    A
    SIHEX
    Sistema de Historico de Extratos
    Data:
    Pagina:
    Cliente:
    Agencia:
    Periodo de solicitacao do Extrato:
    CPF/CNPJ:
    Operacao:
    Conta:
    Data Mov.
    Nr. Doc.
    Historico
    Valor
    Saldo
    SALDO ANTERIOR
    140,63 C
    03/01/2022
    093303
    CR VD CART
    4,93 C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_sihex_historico_extratos_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_caixa_historico_conta_profile() -> None:
    text = """
    CAI
    X
    A
    Extrato Historico da Conta
    Periodo
    Unidade
    Nome da Unidade
    Conta
    Nome do produto
    CPF/CNPJ do Titular
    Titular
    Data Mov.
    Data e Hora
    Nr.Doc.
    Historico
    Taxa (%)
    Valor
    Saldo
    SALDO ANTERIOR
    344.503,88 C
    24/01/2023
    24/01 05:36
    000000000
    CRED CM SALDO PROPRIO MP
    0,177300
    1,17 C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_extrato_historico_conta_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_inter_profile_when_tokens_match() -> None:
    text = """
    BANCO INTER S.A.
    EXTRATO DE CONTA DIGITAL
    agencia: 0001 conta: 26075935-0
    data descricao valor saldo
    16/04/2026 TRANSFERENCIA PIX 950,00 7.371,34
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "inter_statement_ptbr"
    assert result.confidence >= 0.5


def test_infer_pdf_layout_prefers_sicredi_profile_when_tokens_match() -> None:
    text = """
    SICREDI
    EXTRATO CONTA CORRENTE
    cooperativa 1234 conta 98765-1
    data historico valor saldo
    17/04/2026 TED RECEBIDA 2.000,00 9.371,34
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "sicredi_statement_ptbr"
    assert result.confidence >= 0.5


def test_infer_pdf_layout_prefers_sicredi_matricial_paisagem_profile() -> None:
    text = """
    COOP CRED, POUP E INV VALOR SUSTENTAVEL EXTRATO DE CONTA CORRENTE
    JARDIM MEDICA LTDA
    PAG.: 00001
    PERIODO: DE 01/2021 A 12/2021
    DATA DOCUMENTO HISTORICO DEBITO CREDITO SALDO
    S A L D O A N T E R I O R 73.997,11
    04/01/2021 174611538 SICREDI CREDITO ELO 91,05
    04/01/2021 LIQUIDACAO BOLETO 1.678,83
    04/01/2021 REP029 JUROS UTILIZ.CH.ESPECIAL 59,00 71.304,94
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "sicredi_matricial_paisagem_conta_corrente_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_uses_declarative_c6_profile() -> None:
    text = """
    C6 BANK
    Extrato
    Agencia 0001 Conta 123456-7
    Periodo Abril 2024
    Entradas Saidas
    Data Tipo Descricao Valor
    01/04 Outros gastos Debito De Cartao R$ 81,67
    01/04 Saida PIX Pix enviado R$ 225,00
    Saldo do dia
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1"
    assert result.confidence >= 0.7


def test_infer_pdf_layout_uses_declarative_c6_profile_for_real_extracted_pdf_text() -> None:
    text = """
    C6
    BANK
    Extrato
    Periodo 1 de setembro de 2024 ate 30 de setembro de 2024
    Saldo do dia 18 de novembro de 2024 R$ 4,63
    Setembro 2024
    (01/09/2024 - 30/09/2024)
    Entradas
    R$ 8.400,00
    Saidas
    R$ 8.032,41
    Data
    Tipo
    Descricao
    Valor
    02/09
    Entrada PIX
    Pix recebido de
    R$ 1.000,00
    02/09
    Pagamento
    PGTO DE BOLETO
    R$ 58,00
    11/09
    Entrada PIX
    Transferencia recebida de CLIENTE BETA
    R$ 3.000,00
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_ignores_declarative_profile_below_min_score_hint() -> None:
    text = """
    C6 BANK
    Extrato
    Data Tipo Descricao Valor
    01/04 PIX Pix enviado R$ 225,00
    02/04 Pagamento PGTO DE BOLETO R$ 10,00
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "generic_statement_ptbr"
    assert result.used_fallback is True


def test_infer_pdf_layout_prefers_stone_a4_profile_for_real_extracted_pdf_text() -> None:
    text = """
    Extrato de conta corrente
    Emitido em 04 novembro 2025 às 15:21:19
    stone
    Página 1 de 32
    Dados da conta
    Nome
    Documento
    Instituição
    Agência
    Conta
    Stone Instituição de Pagamento S.A.
    Período: de 01/09/2025 a 04/11/2025
    DATA
    TIPO
    DESCRIÇÃO
    VALOR
    SALDO
    CONTRAPARTE
    04/11/25
    Saída
    ATACADO
    Pagamento
    - R$ 3.898,12
    R$ 0,00
    04/11/25
    Entrada
    Transferência | Pix
    R$ 673,87
    R$ 3.898,12
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "stone_extrato_conta_corrente_a4_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_neon_mei_facil_a4_statement_profile() -> None:
    text = """
    BANCO MEI FACIL
    Atualizacao:
    Nome:
    Banco 536
    Neon pagamentos IP
    Agencia:
    Conta:
    Lancamentos
    data
    lancamento
    valor (R$)
    saldos (R$)
    lancamentos
    01/01/2024
    SALDO ANTERIOR
    1.902,45
    02/01/2024
    PAGAMENTO FATURA CARTAO CRED
    (453,02)
    1.449,43
    02/01/2024
    PIX ENVIADO PARA ANGELO
    (1.440,00)
    9,43
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "neon_banco_mei_facil_extrato_a4_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_banco_nordeste_periodo_a4_profile() -> None:
    text = """
    Banco do
    Nordeste
    Extrato de Conta Corrente - no
    período
    Titular:
    Agência/Conta Corrente:
    Saldo Anterior:
    Período: 01/04/2021 até 30/04/2021
    636,44
    Detalhamento do Extrato
    Data
    Histórico
    Documento
    Valor R $
    Saldo R $
    01/04/2021
    LIQUIDO COBRANCA
    SIMPLES
    1308
    2.637,26
    3.273,70
    01/04/2021
    TARIFA COBRANCA
    1304
    - 1,76
    4.406,16
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "banco_do_nordeste_extrato_periodo_a4_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_banco_nordeste_fundos_rentabilidade_profile() -> None:
    text = """
    FUNDOS DE INVESTIMENTOS - RENTABILIDADE ( % )
    PRODUTO REND. MENSAL REND. ANUAL ULT. 12 MESES
    BNB AUTOMATICO FI RF CURTO PRAZO 0,6345 1,4101 10,1181
    MOVIMENTACOES BNB AUTOMATICO
    DIA HISTORICO QUANT. COTAS VALOR COTA VALOR EM R$
    01 SALDO INICIAL 4.983,898 11,448227 57.056,80
    03 APLICACAO 5.617,694 11,452214 64.335,03
    15 RESGATE 5.592,692 11,463803 64.113,52
    15 I.O.F. S/RESGATE 96,44
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "banco_do_nordeste_fundos_investimentos_rentabilidade_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_stone_grouped_lancamento_valor_saldo_profile() -> None:
    text = """
    Extrato de conta corrente
    stone
    Titular
    Instituicao
    Stone Instituicao de Pagamento S.A.
    Documento
    Periodo
    DATA
    TIPO
    LANCAMENTO
    VALOR (R$)
    SALDO (R$)
    CONTRAPARTE
    31/12/2021
    Debito
    CENTRAL PLAST
    Compra com cartao
    Stone
    478,26
    63.173,95
    31/12/2021
    Debito
    ATACADAO 217 AS
    Compra com cartao
    Stone
    681,76
    63.652,21
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "stone_extrato_conta_corrente_lancamento_valor_saldo_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_caixa_siatr_saldos_lancamentos_profile() -> None:
    text = """
    SIATR-SISTEMA DE AUTO ATENDIMENTO REESTRUTURADO
    SALDOS E LANCAMENTOS
    CAIXA
    AG:
    PRODUTO:
    NOME:
    CPF/CNPJ:
    SDO DISP:
    5.740,61C
    SDO TOT:
    5.740,61C
    SDO CTBL:
    5.592,95C
    PERIODO.:
    DATA MOV NR.DOC DESCRICAO
    VALOR
    SALDO
    07/08/25 071551 CRED PIX CHAVE 5.000,00C 7.446,29C
    07/08/25 000000 SALDO DIA 0,00C 1.117,39C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "caixa_siatr_saldos_lancamentos_a4_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_sicoob_creditran_extrato_detalhado_conta_profile() -> None:
    text = """
    SICOOB
    Creditran
    Conta Corrente
    06/02/2025 15:46:18
    Banco:
    Agencia:
    Conta Corrente:
    EXTRATO DETALHADO CONTA
    PERIODO DE 01/01/2025 A 31/01/2025
    Ultimos Lancamentos Saldo anterior:
    -3.317,19
    Data
    Historico
    Documento
    Valor
    Saldo
    31/12/2024
    DEB.SEGURO EMPRESTIMO
    0009731610
    -45,41
    -3.362,60
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "sicoob_creditran_extrato_detalhado_conta_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_sicoob_poupanca_cooperada_profile() -> None:
    text = """
    SICOOB - Sistema de Cooperativas de Credito do Brasil
    Plataforma de Servicos Financeiros do Sicoob - SISBR
    Extrato Poupanca Cooperada
    Agencia:
    Conta:
    Data
    Documento
    Historico
    Debito
    Credito
    Saldo
    01/10/2022
    SALDO ANTERIOR
    338,38+
    11/10/2022
    CORRECAO MONETARIA
    - SELIC
    0,24+
    338,62+
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "sicoob_extrato_poupanca_cooperada_v1"
    assert result.used_fallback is False


def test_infer_pdf_layout_prefers_sicoob_apropriacao_diaria_profile() -> None:
    text = """
    - SICOOB -
    Sistema de Cooperativas de Credito do Brasil
    Plataforma de Servicos Financeiros do Sicoob - SISBR
    Extrato de Apropriacao Diaria
    01/08/2023
    10:38:02
    MODALIDADE:
    RDC - LONGO POS CDI
    N APPLICACAO:
    DATA FIM DA CARENCIA/VENC.:
    DATA DA APLICACAO:
    Data
    Historico
    Valor
    30/06/2023
    SALDO ANTERIOR
    R$ 7,49C
    04/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    """

    result = infer_pdf_layout(text)

    assert result.layout_name == "sicoob_extrato_apropriacao_diaria_v1"
    assert result.used_fallback is False
