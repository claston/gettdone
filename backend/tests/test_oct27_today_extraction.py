"""Fictional regressions for sparse, unmapped and split-date statement pages."""
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.native_text_quality import assess_native_text_quality
from app.application.parsers.pdf.native_table_source import read_native_table_source
from tests.fixtures.recovery_acceptance import grid_pdf


def banestes_page(*, mismatch=False, missing_month=False):
    return grid_pdf([[
        ("Extrato de Conta Corrente", .38, .10), ("SALDO TOTAL", .40, .14),
        ("ENTRADAS E SAÍDAS", .52, .14), ("R$ 149,00", .38, .17),
        ("R$ 50,00", .60, .17), ("R$ 10,00", .60, .20),
        ("Agência: 0001 Conta: 12345-6", .19, .23),
        ("Período: 01/08/2026 à 31/08/2026", .60, .25),
        ("Data", .08, .29), ("Lançamento", .14, .29), ("Valor (R$)", .87, .29),
        ("Saldo Anterior", .14, .32), ("100,00", .88, .32),
        ("TED Recebida", .15, .35), ("CLIENTE FICTÍCIO", .47, .35),
        ("17", .08, .37), ("AGO/26", .07, .39), ("50,00", .88, .37),
        ("Saldo Conta/Rende+", .14, .40), ("151,00" if mismatch else "150,00", .88, .40),
        ("31", .08, .44), ("XXX/26" if missing_month else "AGO/26", .07, .46),
        ("Pix Enviado", .15, .44), ("FORNECEDOR FICTÍCIO", .47, .44), ("- 10,00", .88, .44),
        ("Saldos", .06, .52), ("Saldo Conta", .14, .55), ("0,00", .90, .55),
        ("Saldo Rende+", .14, .58), ("140,00", .88, .58),
        ("Rendimento Previsto Rende+", .14, .61), ("9,00", .90, .61),
        ("Saldo Total", .14, .64), ("149,00", .88, .64),
    ]])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def test_numeric_font_glyph_names_require_visual_reading():
    text = "/0 /1 /2 /3 /i255 " * 40
    assert assess_native_text_quality(b"", [text]).requires_visual_extraction


def test_dates_and_paths_do_not_trigger_font_name_detection():
    text = "Extrato 01/08/2026 Pix 1.000,00 https://banco.test/123 " * 40
    assert not assess_native_text_quality(b"", [text]).requires_visual_extraction


def test_proven_empty_page_preserves_original_page_number_without_rejecting():
    reader = PdfReader(BytesIO(grid_pdf([[("Extrato 100,00", .1, .1)]])))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.add_blank_page(width=595, height=842)
    stream = BytesIO()
    writer.write(stream)
    source = read_native_table_source(stream.getvalue())
    assert [page.page_number for page in source.pages] == [1, 2]
    assert source.pages[1].lines == []
    assert source.metrics["empty_native_pages_verified"] == 1


def test_image_only_page_cannot_be_skipped_as_empty():
    from PIL import Image

    image = Image.new("RGB", (600, 800), "white")
    image.paste("black", (50, 50, 550, 70))
    stream = BytesIO()
    image.save(stream, format="PDF")
    with pytest.raises(InvalidFileContentError):
        read_native_table_source(stream.getvalue())


def test_split_day_month_and_combined_balance_keep_movements_and_exclude_projected_yield():
    parsed = pdf_parser.parse_pdf_transactions(banestes_page())
    assert [row.date for row in parsed.transactions] == ["2026-08-17", "2026-08-31"]
    assert [row.amount for row in parsed.transactions] == [50, -10]
    assert "CLIENTE FICTÍCIO" in parsed.transactions[0].description
    assert parsed.parse_metrics["financial_controls_validated"] == 1
    assert parsed.parse_metrics["verified_opening_balance"] == 100
    assert parsed.parse_metrics["verified_closing_balance"] == 140
    assert parsed.parse_metrics["balance_bounds_unavailable"] == 1
    assert "combined_cash_investment_balance_not_comparable" in parsed.canonical_transactions[0].warnings
    assert all(row.source_line_refs for row in parsed.canonical_transactions)


@pytest.mark.parametrize("kwargs", [{"mismatch": True}, {"missing_month": True}])
def test_banestes_missing_date_or_wrong_daily_balance_is_not_silently_accepted(kwargs):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(banestes_page(**kwargs))
