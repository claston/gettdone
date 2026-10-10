import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import _resolve_ofx_account_type
from app.application.ofx_writer import build_ofx_statement

INTER_EXAMPLE_CASES = {
    "movimentacoes_tabela": {
        "profile": "banco_inter_extrato_conta_corrente_movimentacoes_v1",
        "amounts": [-700.0, -450.0, 200.0, 1100.0, 3000.0],
        "balances": [542.12, 92.12, 292.12, 1392.12, 4392.12],
        "text": """
        banco Inter Extrato Conta Corrente
        Nome Agência Conta Tipo: Conta Corrente
        Saldo Atual R$ 2.728,77 Depósito de Cheque R$ 0,00 Saldo Disponível R$ 2.728,77
        Movimentações
        Data Lançamento Lançamentos Valor Saldo
        05/04/2021 PAGAMENTO DE TITULO - pagamento - R$ 700,00 542,12
        05/04/2021 PIX ENVIADO - Cp: 60746948-2595 - R$ 450,00 92,12
        05/04/2021 PIX RECEBIDO - Cp: 60746948-3291-89333 R$ 200,00 292,12
        06/04/2021 PIX RECEBIDO - Cp: 24654881-0911 R$ 1.100,00 1.392,12
        07/04/2021 PIX RECEBIDO - Cp: 24654881-0911-7883 R$ 3.000,00 4.392,12
        """,
    },
    "lista_diaria": {
        "profile": "banco_inter_extrato_conta_corrente_lista_diaria_v1",
        "amounts": [-9999.99, -33.33, -404.0],
        "text": """
        inter
        CPF/CNPJ Instituição: Banco Inter Agência Conta
        Tipo: Conta Corrente Saldo atual
        Período: 01/09/2022 a 30/09/2022
        28 de Setembro de 2022
        Pix enviado Empreendimentos -R$ 9.999,99
        Compra no débito Drogaria -R$ 33,33
        27 de Setembro de 2022
        Pix enviado Sheila -R$ 404,00
        """,
    },
    "fatura_cartao": {
        "profile": "banco_inter_fatura_cartao_despesas_v1",
        "amounts": [-158.99, -16.94, -1.02, -16.0, 5585.68, -1.02, -16.0],
        "text": """
        inter Sua fatura de novembro chegou VENCIMENTO 10/11/2022
        Resumo da fatura Despesas da fatura CARTÃO
        DATA MOVIMENTAÇÃO VALOR
        14 set 2022 Americanas (Parcela 02 De 03) R$ 158,99
        14 set 2022 Americanas (Parcela 02 De 03) R$ 16,94
        06 out 2022 Iof Internacional R$ 1,02
        06 out 2022 Linktree* Linktree R$ 16,00
        Valor e símbolo da moeda origem: 3,11 USD Valor em dólar americano: $ 3,11
        10 out 2022 Pagamento On Line + R$ 5.585,68
        19 out 2022 Iof Internacional R$ 1,02
        19 out 2022 Linktree* Linktree R$ 16,00
        """,
    },
    "saldo_por_transacao": {
        "profile": "banco_inter_extrato_conta_corrente_saldo_transacao_v1",
        "amounts": [717.64, 20.0, -715.82, 35.0, 30.0, 125.0],
        "balances": [743.51, 763.51, 47.69, 82.69, 112.69, 237.69],
        "text": """
        inter CPF/CNPJ
        Período: 01/08/2023 a 31/08/2023
        Saldo total R$ 475,88 Saldo disponível R$ 475,88 Saldo bloqueado R$ 0,00
        1 de Agosto de 2023 Saldo do dia Valor Saldo por transação
        TED RECEBIDA - 197 260760 PAGAR.ME PAGAMENTOS S.A. R$ 717,64 R$ 743,51
        PIX RECEBIDO - Cp: 18236120 R$ 20,00 R$ 763,51
        PAGAMENTO DE TITULO - Pagamento -R$ 715,82 R$ 47,69
        PIX RECEBIDO - Cp: 60701190 R$ 35,00 R$ 82,69
        PIX RECEBIDO - Cp: 18236120 R$ 30,00 R$ 112,69
        PIX RECEBIDO - Cp: 00360305 R$ 125,00 R$ 237,69
        """,
    },
    "posicao_renda_fixa": {
        "profile": "banco_inter_extrato_posicao_renda_fixa_v1",
        "amounts": [-21000.0, -2500.0, -3500.0, -6000.0],
        "text": """
        inter EXTRATO DE POSIÇÃO DE RENDA FIXA Emissão: 24/10/2025
        Agência Conta Posição em
        CDB LIQUIDEZ DIARIA
        Valor Bruto Total R$ 252.781,35 Valor Líquido Total R$ 245.988,63 Valor Aplicado Total R$ 216.518,98
        Nota Data Início Data Vencimento Valor Aplicação Tipo Aplicação Taxa Aplicação
        Valor Rendimento Valor Retirada Valor Desconto Valor Bruto Valor Previsão Desconto Valor Líquido IR/IOF
        122047330 17/01/2024 07/01/2026 R$ 21.000,00 CDB 100% do CDI R$ 363,27 R$ 2.488,76
        123687422 23/01/2024 13/01/2026 R$ 2.500,00 CDB 100% do CDI R$ 567,72 R$ 0,00
        125895847 31/01/2024 21/01/2026 R$ 3.500,00 CDB 100% do CDI R$ 783,56 R$ 0,00
        127845453 08/02/2024 27/01/2026 R$ 6.000,00 CDB 100% do CDI R$ 1.330,81 R$ 0,00
        """,
    },
}


@pytest.mark.parametrize("case_name", INTER_EXAMPLE_CASES)
def test_parse_pdf_transactions_supports_banco_inter_visual_examples(case_name: str, monkeypatch) -> None:
    case = INTER_EXAMPLE_CASES[case_name]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [case["text"]])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [case["text"]])

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == case["profile"]
    assert [transaction.amount for transaction in result.transactions] == case["amounts"]
    if "balances" in case:
        assert [transaction.running_balance for transaction in result.canonical_transactions] == case["balances"]
    ofx = build_ofx_statement(result.transactions, account_type="credit_card" if case_name == "fatura_cartao" else None)
    assert ofx.count("<STMTTRN>") == len(case["amounts"])
    assert all(f"<TRNAMT>{amount:.2f}" in ofx for amount in case["amounts"])


def test_banco_inter_invoice_resolves_credit_card_ofx_account_type() -> None:
    case = INTER_EXAMPLE_CASES["fatura_cartao"]

    account_type = _resolve_ofx_account_type(
        extension="pdf",
        filename="fatura-inter.pdf",
        raw_bytes=b"%PDF synthetic",
        extracted_text=case["text"],
        layout_inference_name=case["profile"],
    )

    assert account_type == "credit_card"


def test_banco_inter_running_balance_preserves_negative_currency_prefixes(monkeypatch) -> None:
    text = """
    inter CPF/CNPJ
    Período: 01/08/2023 a 31/08/2023
    Saldo total R$ 0,00 Saldo disponível R$ 0,00 Saldo bloqueado R$ 0,00
    1 de Agosto de 2023 Saldo do dia Valor Saldo por transação
    PIX RECEBIDO R$ 100,00 R$ 100,00
    PIX ENVIADO -R$ 650,00 -R$ 550,00
    PIX RECEBIDO R$ 500,00 -R$ 50,00
    PIX RECEBIDO R$ 50,00 R$ 0,00
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [text])

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "banco_inter_extrato_conta_corrente_saldo_transacao_v1"
    assert [transaction.amount for transaction in result.transactions] == [100.0, -650.0, 500.0, 50.0]
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        100.0,
        -550.0,
        -50.0,
        0.0,
    ]
    assert result.parse_metrics["balance_consistency_checked"] == 3
    assert result.parse_metrics["balance_consistency_failed"] == 0
