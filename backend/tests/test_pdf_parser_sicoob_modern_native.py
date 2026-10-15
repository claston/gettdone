from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.pdf_layout_inference import infer_pdf_layout

SICOOB_MODERN_LAYOUT = "sicoob_extrato_conta_corrente_moderno_v1"


SICOOB_MODERN_NATIVE_TEXT = """
SICOOB SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL
PLATAFORMA DE SERVICOS FINANCEIROS DO SICOOB - SISBR
EXTRATO DE CONTA CORRENTE
Cooperativa 0000 Conta 00000-0 Periodo 01/08/2026 - 31/08/2026
HISTORICO DE MOVIMENTACAO
Data     Documento     Historico                                      Valor
03/08   100001        PIX RECEBIDO                                  R$ 500,00C
03/08   100002        DEB.TARIFA SERVICO                            R$ 100,00D
03/08   100003        PAGAMENTO                                     R$ 100,00D
03/08                 SALDO DO DIA                                  R$ 300,00C
04/08   100004        DEB.TRANSFERENCIA                             R$ 100,00D
04/08                 SALDO DO DIA                                  R$ 200,00C
26/08   100005        PIX RECEBIDO                                R$ 8.625,00C
26/08                 SALDO DO DIA                                R$ 8.825,00C
"""


def test_infer_modern_sicoob_pix_statement_reaches_high_confidence() -> None:
    result = infer_pdf_layout(SICOOB_MODERN_NATIVE_TEXT)

    assert result.layout_name == SICOOB_MODERN_LAYOUT
    assert result.confidence >= 0.95
    assert result.used_fallback is False


def test_parse_modern_sicoob_native_layout_skips_daily_balances() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [SICOOB_MODERN_NATIVE_TEXT],
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == SICOOB_MODERN_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_sicoob"
    assert [transaction.amount for transaction in result.transactions] == [
        500.0,
        -100.0,
        -100.0,
        -100.0,
        8625.0,
    ]
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0


def test_parse_sicoob_history_vertical_ocr_rows_reassigns_leading_amounts() -> None:
    native_text = """
    SICOOB
    SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
    PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
    05/08/2026
    EXTRATO CONTA CORRENTE
    COOP.: 0000-0 / SICOOB TESTE
    CONTA: 00000-0 / EMPRESA TESTE
    PERÍODO: 01/02/2026 - 28/02/2026
    HISTÓRICO DE MOVIMENTAÇÃO
    DATA
    HISTÓRICO
    VALOR
    27/02
    SALDO DO DIA
    29.077,25
    D
    27/02
    PIX EMIT.OUTRA IF
    4.276,80
    D
    Pagamento Pix
    DOC.: Pix
    1.300,00
    27/02
    CRED.TRANSF.CONTAS
    C
    REM.: CLIENTE TESTE
    DOC.: 8468612
    27.983,75
    26/02
    SALDO DO DIA
    D
    26/02
    DEB.TR.CT.DIF.TIT.
    500,00D
    FAV.: FORNECEDOR TESTE
    DOC.: 8463997
    8.761,70
    26/02
    PIX EMIT.OUTRA IF
    D
    Pagamento Pix
    DOC.: Pix
    RESUMO
    (+) SALDO EM CONTA:
    29.077,25D
    VENCIMENTO CHEQUE ESPECIAL EMPRESARIAL:
    16/03/2026
    TAXA CHEQUE ESPECIAL EMPRESARIAL (a.m.):
    7,64%
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([native_text])

    assert result.layout.layout_name == "sicoob_sisbr_extrato_conta_corrente_historico_movimentacao_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_sicoob"
    assert [transaction.date for transaction in result.transactions] == [
        "2026-02-27",
        "2026-02-27",
        "2026-02-26",
        "2026-02-26",
    ]
    assert [transaction.amount for transaction in result.transactions] == [
        -4276.80,
        1300.00,
        -500.00,
        -8761.70,
    ]
    assert [transaction.description for transaction in result.transactions] == [
        "PIX EMIT.OUTRA IF Pagamento Pix DOC.: Pix",
        "CRED.TRANSF.CONTAS REM.: CLIENTE TESTE DOC.: 8468612",
        "DEB.TR.CT.DIF.TIT. FAV.: FORNECEDOR TESTE DOC.: 8463997",
        "PIX EMIT.OUTRA IF Pagamento Pix DOC.: Pix",
    ]
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0


def test_parse_modern_sicoob_column_ordered_ocr_sections() -> None:
    native_text = """
    SICOOB
    SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL
    PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR
    EXTRATO DE CONTA CORRENTE
    Cooperativa: Conta: Periodo:
    04/09/2026 - 16:21:01
    HISTÓRICO DE MOVIMENTAÇÃO
    Data Documento
    03/08
    03/08
    03/08 Pix
    03/08 105045
    03/08 41-1
    03/08
    04/08 29415669
    04/08
    26/08 Pix
    26/08
    RESUMO
    Saldo em conta:
    Histórico
    7) SALDO ANTERIOR
    SALDO BLOQUEADO ANTERIOR
    2) PIX RECEBIDO - OUTRA IF - MESMA TIT.
    Recebimento Pix EMPRESA TESTE
    3) DEB.PARCELAS SUBSC./INTEGR.
    < APLICAÇÃO RDC
    72) SALDO DO DIA
    4) DÉB.TRANSFERÊNCIA POUPANÇA
    0000 - 000000000 EMPRESA TESTE
    72) SALDO DO DIA
    2) PIX RECEBIDO - OUTRA IF
    Recebimento Pix CLIENTE TESTE
    7) SALDO DO DIA
    Cheque especial contratado:
    01/08/2026 - 31/08/2026
    Valor
    R$ 0,00C
    R$ 0,00*
    R$ 500,00C
    R$ 100,00D
    R$ 100,00D
    R$ 300,00C
    R$ 100,00D
    R$ 200,00C
    R$ 8.625,00C
    R$ 8.825,00C
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([native_text])

    assert result.layout.layout_name == SICOOB_MODERN_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_sicoob"
    assert [transaction.date for transaction in result.transactions] == [
        "2026-08-03",
        "2026-08-03",
        "2026-08-03",
        "2026-08-04",
        "2026-08-26",
    ]
    assert [transaction.amount for transaction in result.transactions] == [
        500.0,
        -100.0,
        -100.0,
        -100.0,
        8625.0,
    ]
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
