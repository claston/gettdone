"""Product acceptance: exports preserve legible movements, identity and warnings."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.conversion_quality import assess_conversion_quality
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import InvalidFileContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import bb_outside_header, bradesco_monthly_native, sicredi_combined, xp_investment_pages

pytestmark = pytest.mark.pdf_acceptance


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("scale", [.75, 1, 1.5])
def test_bradesco_wrapped_cells_and_page_continuation_preserve_cash(scale):
    result = pdf_parser.parse_pdf_transactions(bradesco_monthly_native(scale))
    assert [(r.date, r.amount) for r in result.transactions] == [
        ("2026-12-01", 500), ("2026-12-01", -150), ("2026-12-02", -150)]
    assert [r.external_reference_id for r in result.canonical_transactions] == ["1001", "1002", "1003"]
    assert all(r.source_regions for r in result.canonical_transactions)
    assert "RECEBIMENTO CARTÃO" in result.transactions[0].description


def test_bb_header_period_discrepancy_is_review_and_preserves_printed_dates():
    result = pdf_parser.parse_pdf_transactions(bb_outside_header())
    assert [(r.date, r.amount) for r in result.transactions] == [("2026-09-28", -20), ("2026-10-01", -10)]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("continuation_header", [False, True])
def test_bradesco_continuation_and_latest_movements_appendix_keep_month_scope(continuation_header):
    result = pdf_parser.parse_pdf_transactions(bradesco_monthly_native(continuation_header=continuation_header, appendix=True))
    assert [(row.date, row.amount) for row in result.transactions] == [
        ("2026-12-01", 500), ("2026-12-01", -150), ("2026-12-02", -150)]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_bradesco_spaced_native_text_uses_original_pdf_geometry():
    from app.application.parsers.pdf.native_cash_columns import parse_native_cash_columns

    raw = bradesco_monthly_native()
    text = "Extrato Mensal / Por Período Extrato de: Lançamento Dcto. Saldo Anterior"
    parsed, _ = parse_native_cash_columns(raw, [" ".join(text)])
    assert [row.transaction.amount for row in parsed.rows] == [500, -150, -150]


@pytest.mark.parametrize("mismatch", [False, True])
def test_sicredi_combined_ocr_preserves_duplicates_and_warns_on_controls(mismatch):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(sicredi_combined(mismatch=mismatch))
    assert [r.amount for r in result.transactions] == [-20, -20, 50]
    assert all(r.source_regions for r in result.canonical_transactions)
    assert "EMPRESA TESTE" in result.transactions[0].description
    if mismatch:
        assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_sicredi_unreadable_amount_is_not_guessed_from_balance():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(sicredi_combined(bad_amount=True))


def test_sicredi_opening_saldo_near_header_is_not_a_column_heading():
    source = sicredi_combined()
    page = source.pages[0]
    header = next(cell for cell in page.lines if cell.text == "Data")
    lines = [replace(cell, text="SALDO", bbox={**cell.bbox, "top": header.bbox["top"]+.024})
             if cell.text == "SALDO" else cell for cell in page.lines]
    lines = [replace(cell, bbox={**cell.bbox, "top": header.bbox["top"]+.024})
             if cell.text == "100,00" else cell for cell in lines]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [row.amount for row in result.transactions] == [-20, -20, 50]


@pytest.mark.parametrize("skew", [-.025, .025])
def test_sicredi_skew_and_centered_headers_do_not_omit_short_values(skew):
    source = sicredi_combined()
    page = source.pages[0]
    lines = [replace(cell, bbox={**cell.bbox, "top": cell.bbox["top"]+skew*cell.bbox["left"]}) for cell in page.lines]
    lines = [replace(cell, bbox={**cell.bbox, "left": .42}) if cell.text == "Descrição" else cell for cell in lines]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [r.amount for r in result.transactions] == [-20, -20, 50]


def test_sicredi_month_zero_uses_only_one_explicit_month_and_requests_review():
    source = sicredi_combined()
    page = source.pages[0]
    lines = [replace(cell, text=cell.text.replace("04/08/2026", "04/00/2026")) for cell in page.lines]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert result.transactions[-1].date == "2026-08-04"
    assert "manual_review_recommended" in result.canonical_transactions[-1].warnings


@pytest.mark.parametrize("date", ["32/08/2026", "31/00/2026"])
def test_sicredi_invalid_dates_are_never_repaired_to_another_day(date):
    source = sicredi_combined()
    page = source.pages[0]
    lines = [replace(cell, text=cell.text.replace("04/08/2026", date)) for cell in page.lines]
    if date == "31/00/2026":
        lines = [replace(cell, text=cell.text.replace("01/08/2026 a 31/08/2026", "01/08/2026 a 30/09/2026")) for cell in lines]
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


@pytest.mark.parametrize("handwritten", [False, True])
def test_sicredi_merged_money_cells_use_word_geometry_and_reject_handwritten_amount(handwritten):
    source = sicredi_combined()
    page = source.pages[0]
    value = next(cell for cell in page.lines if cell.text == "-20,00")
    balance = next(cell for cell in page.lines if cell.text == "80,00")
    words = [ExtractedWord("value", 1, 1, value.text, bbox=value.bbox, text_type="HANDWRITING" if handwritten else "PRINTED"),
             ExtractedWord("balance", 1, 2, balance.text, bbox=balance.bbox, text_type="PRINTED")]
    combined = replace(value, text="-20,00 80,00", word_ids=("value", "balance"))
    lines = [combined if cell.id == value.id else cell for cell in page.lines if cell.id != balance.id]
    source = replace(source, pages=[replace(page, lines=lines, words=words)])
    if handwritten:
        with pytest.raises(InvalidFileContentError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    else:
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        assert [r.amount for r in result.transactions] == [-20, -20, 50]


@pytest.mark.parametrize("band", ["low", "medium"])
def test_dashboard_quality_cannot_mark_parser_review_as_clean(band):
    result = assess_conversion_quality(status="success", conversion_type="pdf-ofx", transactions_count=20,
        layout_name="sicredi_statement_ptbr", layout_confidence=.99, selected_parser="grouped", parser_confidence_band=band)
    assert result.status == "review"


@pytest.mark.parametrize("mismatch", [False, True])
def test_xp_identity_dates_and_forecasts_are_preserved(mismatch):
    raw = generate_native_text_pdf(tuple(p.splitlines() for p in xp_investment_pages(mismatch=mismatch)))
    parsed = pdf_parser.parse_pdf_transactions(raw)
    data = build_default_conversion_pipeline().run(filename="report.pdf", raw_bytes=raw, analysis_id="an_xp_acceptance",
                                                 pdf_parser=lambda *a, **kw: parsed).analysis_data
    doc = data.financial_document
    assert doc and doc["document_type"] == "investment_statement" and doc["origin"] == "XP Investimentos"
    assert len(doc["events"]) == 2
    assert doc["events"][1]["dates"]["transaction"] == "2026-08-03"
    assert doc["events"][1]["dates"]["settlement"] == "2026-08-05"
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]
    assert data.bank_code != "084"
    assert all(e["amount"] != "-999.00" for e in doc["events"])
    assert any(o["amounts"].get("projected") == "-999.00" for o in doc["observations"])
    assert any(v["status"] == "failed" for v in doc["validations"]) == mismatch
