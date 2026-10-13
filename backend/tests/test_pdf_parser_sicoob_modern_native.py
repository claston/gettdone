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
