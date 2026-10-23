from decimal import Decimal

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.late_recovery_statements import (
    bb_zero_pages,
    caixa_manager,
    mutate_ocr,
    tracked_unicred_pdf,
    unicred_invoice_pages,
    viacredi_pages,
)


def parse(pages):
    return pdf_parser._parse_pdf_transactions_from_page_texts(pages, preserve_layout_spacing=True)


@pytest.mark.parametrize("pages,amounts,opening,closing", [
    (viacredi_pages(), [-150, 200, -10, -20], 100, 120),
    (bb_zero_pages(), [-10, -50, 50, 200, -5], -100, 85),
    (unicred_invoice_pages(), [-60, 5, -5, -20, 100], -100, -80),
])
def test_late_native_exports_validate_signed_rows_and_all_controls(pages, amounts, opening, closing):
    result = parse(pages)
    assert [t.amount for t in result.transactions] == amounts
    assert result.parse_metrics["verified_opening_balance"] == opening
    assert result.parse_metrics["verified_closing_balance"] == closing
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(t.source_page and t.source_line for t in result.canonical_transactions)


@pytest.mark.parametrize("pages,before,after", [
    (viacredi_pages(), "50,00-", "51,00-"),
    (viacredi_pages(), "150,00 D", "150,00 C"),
    (viacredi_pages(), "PAG 2", "PAG 3"),
    (viacredi_pages(), "10,00 D", "ilegível D"),
    (bb_zero_pages(), "110,00 (-)", "111,00 (-)"),
    (bb_zero_pages(), "00/00/0000 14175 Saldo do dia", "00/00/0000 14175 TED"),
    (bb_zero_pages(), "14175 125 BANCO EXEMPLO EMPRESA 200,00 (+)", "14175 125 BANCO EXEMPLO EMPRESA ilegível (+)"),
    (unicred_invoice_pages(), "TOTAL DE CLIENTE R$ 80,00", "TOTAL DE CLIENTE R$ 81,00"),
    (unicred_invoice_pages(), "Anuidade - parcela R$ 5,00", "Anuidade - parcela R$ ilegível"),
    (unicred_invoice_pages(), "Página 3 de 4", "Página 2 de 4"),
])
def test_late_exports_cannot_hide_missing_fields_or_contradictory_controls(pages, before, after):
    changed = [page.replace(before, after) for page in pages]
    if before.startswith(("PAG", "Página")):
        result = parse(changed)
        assert len(result.transactions) == len(parse(pages).transactions)
        assert result.parse_metrics["partial_statement"] == 1
    else:
        with pytest.raises(PdfFinancialValidationError):
            parse(changed)


@pytest.mark.parametrize("rotated", [False, True])
def test_caixa_manager_uses_posting_dates_balance_deltas_and_excludes_daily_controls(rotated):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_manager(rotated=rotated))
    assert [t.amount for t in result.transactions] == [-30, 50]
    assert [t.date for t in result.transactions] == ["2026-09-08", "2026-09-09"]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.source_extraction is not None
    assert all(t.source_regions for t in result.canonical_transactions)


@pytest.mark.parametrize("old,new", [
    ("R$ 50,00", "R$ 51,00"), ("R$ 70,00 C", "R$ 71,00 C"),
    ("R$ 30,00", "ilegível"), ("08/09/2026", "00/09/2026"),
    ("09/09/2026", "09/10/2026"),
])
def test_caixa_manager_rejects_mismatched_amounts_dates_and_balances(old, new):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(mutate_ocr(caixa_manager(rotated=True), old, new))


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_caixa_gateway_selects_the_same_controlled_reader_in_both_modes(mode, monkeypatch):
    from tests.ocr_conversion_http_helpers import configure_ocr

    raw = configure_ocr(monkeypatch, caixa_manager(rotated=True), mode=mode)
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [-30, 50]
    assert result.parse_metrics["textract_used"] == 1
    assert result.source_extraction is not None


@pytest.mark.parametrize("pages,count,bank,account_type,closing", [
    (viacredi_pages(), 4, "085", None, 120),
    (bb_zero_pages(), 5, "001", None, 85),
    (unicred_invoice_pages(), 5, "136", "credit_card", -80),
])
def test_native_pipeline_preserves_financial_identity_and_never_uses_credit_limit(
    pages, count, bank, account_type, closing, monkeypatch,
):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in pages))
    result = build_default_conversion_pipeline().run(filename="input.pdf", raw_bytes=raw, analysis_id="an_late")
    a = result.analysis_data
    assert a.transactions_total == count
    assert a.bank_code == bank
    assert a.ofx_account_type == account_type
    assert a.closing_balance == closing
    assert Decimal(str(a.opening_balance)) + Decimal(str(a.total_inflows)) + Decimal(str(a.total_outflows)) == Decimal(str(closing))
    if account_type:
        assert a.document_type == "credit_card_statement"


@pytest.mark.parametrize("scale", [.75, 1, 2])
def test_tracked_invoice_coordinates_keep_late_negative_objects_on_their_own_rows(scale, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(tracked_unicred_pdf(scale=scale))
    assert [t.amount for t in result.transactions] == [-60, 5, -5, -20, 100]
    assert "Pagamento Recebido" in result.transactions[-1].description
    assert result.transactions[-1].date == "2025-12-07"
    assert result.parse_metrics["verified_closing_balance"] == -80


def test_tracked_invoice_missing_bonus_cannot_be_silently_dropped(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(tracked_unicred_pdf(missing_amount=True))


@pytest.mark.parametrize("pages", [viacredi_pages()[:1], viacredi_pages()[1:], unicred_invoice_pages()[:-1]])
def test_incomplete_native_statements_cannot_pass_financial_validation(pages):
    if "UNICRED" in "\n".join(pages).upper():
        result = parse(pages)
        assert len(result.transactions) == 5
        assert result.parse_metrics["partial_statement"] == 1
    else:
        with pytest.raises(PdfFinancialValidationError):
            parse(pages)


def test_bb_retains_split_source_references_and_never_assigns_a_zero_date_to_a_transaction():
    result = parse(bb_zero_pages())
    split = result.canonical_transactions[3]
    assert split.source_page == 1
    assert (2, 4) in split.source_line_refs
    assert all(t.date != "0000-00-00" for t in result.transactions)
    assert "IOF Saldo Devedor Conta" in result.transactions[0].description
