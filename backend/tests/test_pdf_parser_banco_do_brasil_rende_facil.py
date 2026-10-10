from __future__ import annotations

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement
from app.application.pdf_layout_inference import infer_pdf_layout

BB_RENDE_FACIL_LAYOUT = "banco_do_brasil_rende_facil_historico_movimentacao_v1"


def _sample_pages() -> list[str]:
    return [
        """
        Histórico de movimentação
        Data Histórico Capital Rendimento* IR IOF Valor Líquido
        31/08/2026 Saldo Anterior 1.000,00 25,00 0,00 0,00 1.025,00
        01/09/2026 Aplicação 100,00 0,00 0,00 0,00 100,00
        02/09/2026 Resgate 80,00 5,00 1,00 0,50 83,50
        BB Rende Fácil
        Cliente: [TITULAR]
        Agência: [AGÊNCIA]
        Conta: [CONTA]
        """,
        """
        Data Histórico Capital Rendimento* IR IOF Valor Líquido
        03/09/2026 Aplicação 250,00 0,00 0,00 0,00 250,00
        04/09/2026 Resgate 200,00 12,00 2,00 1,00 209,00
        30/09/2026 Saldo Final 870,00 32,00 0,00 0,00 902,00
        """,
    ]


def test_recognizes_bb_rende_facil_movement_history_layout() -> None:
    result = infer_pdf_layout("\n".join(_sample_pages()))

    assert result.layout_name == BB_RENDE_FACIL_LAYOUT
    assert result.confidence >= 0.9
    assert result.used_fallback is False


def test_parses_bb_rende_facil_cash_movements_and_ignores_balances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = _sample_pages()
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: pages)
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: pages)

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == BB_RENDE_FACIL_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_banco_do_brasil_rende_facil"
    assert [(row.date, row.description, row.amount) for row in result.transactions] == [
        ("2026-09-01", "APLICACAO BB RENDE FACIL", -100.0),
        ("2026-09-02", "RESGATE BB RENDE FACIL", 83.5),
        ("2026-09-03", "APLICACAO BB RENDE FACIL", -250.0),
        ("2026-09-04", "RESGATE BB RENDE FACIL", 209.0),
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
    assert build_ofx_statement(result.transactions).count("<STMTTRN>") == 4
