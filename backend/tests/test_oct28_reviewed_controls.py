"""Source controls are metadata; dates and counterparties are not pagination."""

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.application import pdf_parser
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import bradesco_monthly_native, grid_pdf
from tests.test_oct28_new_statement_failures import banrisul_month_source
from tests.test_recovery_sweep_ocr import extraction


@pytest.mark.parametrize(
    "text",
    [
        "Extrato por período\nREF:8/2026 20/08 PIX TESTE 10,00\nAtualizado hoje",
        "Extrato por período\nREF:9/2026 18/09 PIX TESTE 10,00\nAtualizado hoje",
        "Itaú Empresas\nLançamentos\nPIX RECEBIDO MERCADO PAGO\n19/10\nPIX TESTE 10,00\nFim",
    ],
)
def test_transaction_dates_and_counterparty_banks_are_not_page_counters(text):
    assert not validate_statement_completeness([text])


def test_real_mercado_pago_footer_still_reports_a_missing_page():
    assert validate_statement_completeness(["EXTRATO DE CONTA Mercado Pago\nwww.mercadopago.com.br\n1/3"])


def test_generic_opening_is_not_a_financial_movement(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(
        (
            [
                "Extrato de Conta Corrente BANCO TESTE",
                "Data Histórico Valor Saldo",
                "31/08/2026 SALDO ANTERIOR 100,00 C",
                "01/09/2026 PIX RECEBIDO 50,00 C 150,00 C",
                "02/09/2026 PIX ENVIADO 20,00 D 130,00 C",
            ],
        )
    )
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [50, -20]
    assert all("SALDO ANTERIOR" not in t.description for t in result.transactions)


def test_missing_final_pages_preserve_bradesco_rows_with_review(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    reader = PdfReader(BytesIO(bradesco_monthly_native()))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    buffer = BytesIO()
    writer.write(buffer)
    result = pdf_parser.parse_pdf_transactions(buffer.getvalue())
    assert [t.amount for t in result.transactions] == [500]
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_banrisul_native_day_merged_with_history_remains_readable(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    source = banrisul_month_source()
    raw = grid_pdf([[(line.text, line.bbox["left"], line.bbox["top"]) for line in page.lines] for page in source.pages])
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [-20, 50, 0.01]


def credisis_hyphen_source():
    return "\n".join(
        [
            "CREDISIS",
            "Data Código Descrição Valor Saldo",
            "Saldo final R$ 150,00",
            "02/09/2026 1001 PIX RECEBIDO TESTE - R$ 50,00 R$ 150,00",
            "Saldo anterior R$ 100,00",
            "Saldos em 30/09/2026",
            "Ouvidoria CREDISIS",
        ]
    )


def test_history_hyphen_is_not_a_negative_sign_before_currency(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = grid_pdf(
        [
            [
                ("Ouvidoria CREDISIS", 0.05, 0.03),
                ("Data", 0.1, 0.1),
                ("Código", 0.21, 0.1),
                ("Descrição", 0.3, 0.1),
                ("Valor", 0.7, 0.1),
                ("Saldo", 0.83, 0.1),
                ("Saldo final", 0.3, 0.15),
                ("R$ 150,00", 0.83, 0.15),
                ("02/09/2026", 0.1, 0.25),
                ("REF001", 0.21, 0.25),
                ("PIX RECEBIDO TESTE -", 0.3, 0.25),
                ("R$ 50,00", 0.7, 0.25),
                ("R$ 150,00", 0.83, 0.25),
                ("Saldo anterior", 0.3, 0.35),
                ("R$ 100,00", 0.83, 0.35),
                ("Saldos em 30/09/2026", 0.1, 0.45),
            ]
        ]
    )
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [50]
    assert result.parse_metrics["verified_closing_balance"] == 150
    assert not result.parse_metrics.get("statement_validation_failed")


def test_bnb_card_debit_receivable_respects_printed_credit_sign(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    source = extraction(
        [
            ("Banco do Nordeste Extrato de Conta Corrente", 0.05, 0.02),
            ("Período: janeiro/2022", 0.05, 0.05),
            ("Data", 0.05, 0.1),
            ("Histórico", 0.25, 0.1),
            ("Documento", 0.6, 0.1),
            ("Valor R$", 0.8, 0.1),
            ("Saldo anterior", 0.25, 0.15),
            ("0,00 C", 0.8, 0.15),
            ("05/01/2022", 0.05, 0.25),
            ("RECEBIVEIS CARTAO DEBITO", 0.25, 0.25),
            ("10863", 0.6, 0.25),
            ("182,02 C", 0.8, 0.25),
            ("05/01/2022", 0.05, 0.35),
            ("APLICACAO FUNDO BN", 0.25, 0.35),
            ("52639", 0.6, 0.35),
            ("182,02 D", 0.8, 0.35),
        ]
    )
    raw = grid_pdf([[(line.text, line.bbox["left"], line.bbox["top"]) for line in source.pages[0].lines]])
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [182.02, -182.02]
