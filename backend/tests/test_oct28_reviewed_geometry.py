"""Column and page evidence for balances, signs and continued savings rows."""

import pytest

from app.application import pdf_parser
from app.application.errors import MaxPagesPerFileExceededError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from app.application.parsers.pdf.reviewed_cash_columns import parse_reviewed_cash
from app.application.parsers.pdf.sicredi_visual_cash import parse_sicredi_visual_cash
from app.application.parsers.pdf.statement_review import statement_review_scope
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_embedded_ocr_quality import layered_pdf, visual_result
from tests.test_recovery_sweep_ocr import extraction


def test_printed_positive_card_settlement_is_a_credit_regardless_of_history():
    source = extraction(
        [
            ("Data", 0.08, 0.2),
            ("Histórico", 0.16, 0.2),
            ("Valor (R$)", 0.71, 0.2),
            ("Saldo (R$)", 0.87, 0.2),
            ("SALDO ANTERIOR", 0.16, 0.15),
            ("100,00", 0.88, 0.15),
            ("02/09/2026", 0.05, 0.3),
            ("Pagamento Cartao De Credito", 0.16, 0.3),
            ("50,00", 0.73, 0.3),
            ("150,00", 0.88, 0.3),
        ]
    )
    statement = parse_reviewed_cash(source, "santander_app")
    assert [r.transaction.amount for r in statement.rows] == [50]
    assert statement.opening == 100 and statement.closing == 150


def test_caixa_daily_control_is_not_a_transaction():
    source = extraction(
        [
            ("Saldo anterior ao período solicitado", 0.06, 0.15),
            ("R$ 100,00 C", 0.85, 0.15),
            ("Valor", 0.68, 0.2),
            ("Saldo", 0.90, 0.2),
            ("01/09/2026", 0.06, 0.3),
            ("PIX RECEBIDO", 0.25, 0.309),
            ("R$ 50,00", 0.68, 0.309),
            ("R$ 150,00 C", 0.85, 0.309),
            ("01/09/2026", 0.06, 0.4),
            ("SALDO DIA", 0.25, 0.4),
            ("R$ 150,00 C", 0.85, 0.4),
        ]
    )
    statement = parse_reviewed_cash(source, "caixa_enriched")
    assert len(statement.rows) == 1
    assert statement.rows[0].transaction.amount == 50
    assert statement.printed_balance_checks == 2


def sicredi_visual_source(*, balance="150,00", value="50,00"):
    raw = grid_pdf(
        [
            [
                ("Sicredi", 0.20, 0.04),
                ("Dados referentes ao período 01/09/2026 a 30/09/2026", 0.20, 0.08),
                ("Cooperativa: 0001", 0.2, 0.12),
                ("Data", 0.22, 0.17),
                ("Descrição", 0.41, 0.17),
                ("Documento", 0.61, 0.17),
                ("Valor(R$)", 0.69, 0.17),
                ("Saldo (R$)", 0.76, 0.17),
                ("SALDO", 0.27, 0.20),
                ("100,00", 0.76, 0.20),
                ("01/09/2026", 0.20, 0.25),
                ("RECEBIMENTO PIX", 0.29, 0.25),
                ("PIX CRED", 0.61, 0.25),
                (value, 0.69, 0.25),
                (balance, 0.76, 0.25),
            ]
        ]
    )
    return read_native_table_source(raw)


def test_visual_sicredi_uses_value_column_and_separate_opening_control():
    statement = parse_sicredi_visual_cash(sicredi_visual_source())
    assert [r.transaction.amount for r in statement.rows] == [50]
    assert statement.opening == 100 and statement.closing == 150


def test_unreadable_balance_preserves_a_readable_sicredi_movement_for_review():
    with statement_review_scope() as warnings:
        statement = parse_sicredi_visual_cash(sicredi_visual_source(balance="I50,00"))
    assert [r.transaction.amount for r in statement.rows] == [50]
    assert statement.closing is None
    assert statement.balance_bounds_unavailable
    assert "financial_validation_failed" in warnings


def test_embedded_glyph_recovery_obeys_existing_ocr_page_limit(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    raw = layered_pdf("Extrato\n01/09/2026 PIX i506,95 1.000,00\n02/09/2026 PIX •420,00 580,00", pages=2)
    calls = []
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", lambda *a, **kw: calls.append(True) or visual_result())
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(raw, max_ocr_pages=1)
    assert not calls
    result = pdf_parser.parse_pdf_transactions(raw, max_ocr_pages=2)
    assert calls == [True]
    assert result.parse_metrics["native_text_quality_requires_visual"] == 1


def test_savings_row_continued_on_next_page_preserves_real_amount_and_both_sources(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = grid_pdf(
        [
            [
                ("Extratos - Poupança", 0.3, 0.07),
                ("POUPANÇA-OURO", 0.1, 0.1),
                ("Dt. base", 0.18, 0.25),
                ("31/08/2026", 0.096, 0.283),
                ("Saldo anterior", 0.285, 0.283),
                ("100,00", 0.874, 0.277),
                ("C", 0.874, 0.289),
                ("01/09/2026", 0.096, 0.5),
                ("615 Pix - Recebido", 0.285, 0.5),
                ("10,00", 0.826, 0.5),
                ("C", 0.826, 0.511),
                ("615 Pix - Recebido QR", 0.285, 0.945),
                ("20,00", 0.826, 0.945),
            ],
            [
                ("02/09/2026", 0.096, 0.043),
                ("Code CLIENTE TESTE", 0.285, 0.043),
                ("C", 0.826, 0.043),
                ("Saldo atual", 0.096, 0.12),
                ("Saldo total", 0.096, 0.16),
                ("130,00", 0.582, 0.16),
                ("C", 0.67, 0.16),
            ],
        ]
    )
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [r.amount for r in result.transactions] == [10, 20]
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert {page for page, line in result.canonical_transactions[1].source_line_refs} == {1, 2}
