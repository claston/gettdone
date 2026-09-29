from __future__ import annotations

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.pdf_layout_inference import infer_pdf_layout


def _parse_pages(monkeypatch: pytest.MonkeyPatch, pages: list[str]):
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: pages)
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: pages)
    return pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")


def test_recognizes_privacy_sanitized_bb_cdb_capture_with_clean_confidence() -> None:
    result = infer_pdf_layout(
        """
        [TITULAR]
        EXTRATOS - CDB / RDB E BB REAPLIC
        AGENCIA [AGENCIA]
        CONTA [CONTA]
        PERIODO 01/04/2026 A 30/04/2026
        DATA DT.PROC HISTORICO NR.DEPOSITO VALOR
        01/04 RESGATE - [IDENTIFICADOR]
        VALOR CAPITAL 19.000,00
        VALOR JUROS ATE MES ANT 269,04
        VALOR JUROS NO MES 9,88
        VALOR IR 62,70-
        VALOR LIQUIDO 19.216,22
        30/04 RENDIMENTO MENSAL - [IDENTIFICADOR]
        VALOR JUROS 944,82
        RENDIMENTO BRUTO NO PERIODO POR DEPOSITO
        """
    )

    assert result.layout_name == "banco_do_brasil_cdb_rdb_bb_reaplic_v1"
    assert result.confidence >= 0.95
    assert result.used_fallback is False


def test_parses_bb_cdb_movements_and_ignores_position_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _parse_pages(
        monkeypatch,
        [
            """
            Banco do Brasil
            Extratos - CDB / RDB e BB Reaplic
            Período 01/11/2024 a 29/11/2024
            BB CDB DI
            Data Dt.proc Histórico Nr.depósito Valor
            31/10 Saldo anterior
            valor capital 0,00
            05/11 Aplicação - 0910058285917
            valor capital 4.555.500,00
            28/11 Resgate - 0910058285917
            valor capital 271.500,00
            valor juros no mês 1.634,43
            valor IR 282,36-
            valor IOF 374,67-
            valor líquido 272.477,40
            29/11 Rendimento mensal - 0910058285917
            valor juros 27.503,28
            29/11 Saldo final
            valor capital 4.284.000,00
            SALDO NOS ULTIMOS 6 MESES
            Data Capital em ser Juros IR proj. Liquid.proj.
            29/11/2024 4284000,00 27503,28 4883,76 4306619,52
            RESUMO DOS DEPOSITOS EM SER
            Numero Dt.aplic Capital Inicial Saldo de Capital Taxa Dt.vcto
            0910058285917 05/11/2024 4.555.500,00 4.284.000,00 96,00 10/10/2029
            RENDIMENTO BRUTO NO PERIODO POR DEPOSITO
            Data Nr. depósito Rend.bruto
            29/11 0910058285917 27.503,28
            """
        ],
    )

    assert result.layout.layout_name == "banco_do_brasil_cdb_rdb_bb_reaplic_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "layout_specific_banco_do_brasil_cdb"
    assert [transaction.date for transaction in result.transactions] == [
        "2024-11-05",
        "2024-11-28",
        "2024-11-29",
    ]
    assert [transaction.description for transaction in result.transactions] == [
        "APLICACAO CDB",
        "RESGATE CDB",
        "RENDIMENTO MENSAL CDB",
    ]
    assert [transaction.amount for transaction in result.transactions] == [
        -4555500.0,
        272477.40,
        27503.28,
    ]
    assert [row.external_reference_id for row in result.canonical_transactions or []] == [
        "0910058285917",
        "0910058285917",
        "0910058285917",
    ]


def test_keeps_bb_cdb_resgate_open_across_page_break(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _parse_pages(
        monkeypatch,
        [
            """
            Banco do Brasil
            Extratos - CDB / RDB e BB Reaplic
            Período 01/04/2026 a 30/04/2026
            BB CDB DI
            Data Dt.proc Histórico Nr.depósito Valor
            27/04 Resgate - 4810047242595
            """,
            """
            valor capital 60.000,00
            valor juros até mês ant 405,60
            valor juros no mês 531,60
            valor IR 210,00-
            valor líquido 60.727,20
            30/04 Rendimento mensal - 4810047242595
            valor juros 944,82
            30/04 Saldo final
            valor capital 735.000,00
            """,
        ],
    )

    assert [(transaction.date, transaction.amount) for transaction in result.transactions] == [
        ("2026-04-27", 60727.20),
        ("2026-04-30", 944.82),
    ]
    assert result.parse_metrics["canonical_external_reference_coverage_rate"] == 1.0
