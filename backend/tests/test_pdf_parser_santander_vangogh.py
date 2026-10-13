from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.pdf_layout_inference import infer_pdf_layout

SANTANDER_VANGOGH_LAYOUT = "santander_vangogh_resumo_consolidado_conta_corrente_v1"


SANTANDER_VANGOGH_LAYOUT_TEXT = """
Santander
EXTRATO CONSOLIDADO
Resumo - maio/2026
Nome
Agencia
Conta Corrente
(-) Saldo de Conta Corrente em 30/04                                      100,00
(=) Saldo de Conta Corrente em 31/05                                      150,01
(=) Saldo Disponivel de Conta Corrente                                    150,01
Limite Santander Master                                                     0,00
Saldo Disponivel Total em 31/05                                           150,01

Conta Corrente
Movimentacao
Data             Descricao                     Documento      Movimento (R$)       Saldo (R$)
SALDO EM 30/04                                                                        100,00
04/05            PIX RECEBIDO                  123456                 50,00
                 PAGAMENTO DE BOLETO           -                      20,00-
                 REMUNERACAO APLICACAO         -                       0,01             130,01
05/05            TARIFA BANCARIA               654321                 10,00-
                 TRANSFERENCIA RECEBIDA        789012                 30,00             150,01
SALDO EM 31/05                                                                        150,01
SALDOS POR PERIODO
"""


SANTANDER_VANGOGH_FLAT_TEXT = """
Santander
EXTRATO CONSOLIDADO
Resumo - maio/2026
Nome
Agencia
Conta Corrente
Saldo de Conta Corrente em 30/04
Saldo de Conta Corrente em 31/05
Saldo Disponivel de Conta Corrente
Limite Santander Master
Saldo Disponivel Total em 31/05
Conta Corrente
Movimentacao
Data Descricao Documento Movimento (R$) Saldo (R$)
SALDO EM 30/04 100,00
04/05 PIX RECEBIDO 123456 50,00
05/05 TARIFA BANCARIA 654321 10,00-
SALDO EM 31/05 150,01
SALDOS POR PERIODO
"""


def test_infer_vangogh_export_without_brand_label_reaches_high_confidence() -> None:
    result = infer_pdf_layout(SANTANDER_VANGOGH_LAYOUT_TEXT)

    assert result.layout_name == SANTANDER_VANGOGH_LAYOUT
    assert result.confidence >= 0.95
    assert result.used_fallback is False


def test_parse_vangogh_layout_inherits_grouped_dates_and_validates_balances() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [SANTANDER_VANGOGH_LAYOUT_TEXT],
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == SANTANDER_VANGOGH_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_santander_vangogh"
    assert [transaction.date for transaction in result.transactions] == [
        "2026-05-04",
        "2026-05-04",
        "2026-05-04",
        "2026-05-05",
        "2026-05-05",
    ]
    assert [transaction.amount for transaction in result.transactions] == [
        50.0,
        -20.0,
        0.01,
        -10.0,
        30.0,
    ]
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0


def test_parse_pdf_recovers_complete_vangogh_native_layout(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda _raw_bytes: [SANTANDER_VANGOGH_FLAT_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda _raw_bytes: [SANTANDER_VANGOGH_LAYOUT_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module.text_extraction,
        "read_pdf_creation_month_year",
        lambda _raw_bytes: (5, 2026),
    )

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic Santander")

    assert result.layout.layout_name == SANTANDER_VANGOGH_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_santander_vangogh"
    assert len(result.transactions) == 5
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
