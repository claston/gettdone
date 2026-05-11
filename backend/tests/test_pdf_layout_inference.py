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
