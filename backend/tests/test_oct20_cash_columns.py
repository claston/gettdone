import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.positioned_cash_statements import bradesco_pages, change_cells, positioned_pdf, santander_pages


@pytest.mark.parametrize("kind", ["unified", "movements"])
@pytest.mark.parametrize("shuffled", [False, True])
def test_bradesco_native_columns_keep_dateless_rows_signs_references_and_cash_balance(kind, shuffled, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = positioned_pdf(bradesco_pages(kind), shuffled=shuffled)
    parsed = parse_pdf_transactions(raw)
    a = build_default_conversion_pipeline().run(filename="bradesco.pdf",
        raw_bytes=raw, analysis_id="an_cash_columns", pdf_parser=lambda *a, **kw: parsed).analysis_data
    assert [r.amount for r in a.report_transactions] == [2, 200, -202]
    assert [r.date for r in a.report_transactions] == ["2026-09-01"] * 3
    assert [r.external_reference_id for r in parsed.canonical_transactions] == ["000001", "000002", "000003"]
    assert "DESTINATARIO" in a.report_transactions[-1].description
    assert a.opening_balance == a.closing_balance == 1
    assert a.bank_code == "237"
    assert a.document_type == "bank_statement"
    assert a.pdf_processing_metrics["balance_consistency_failed"] == 0
    assert a.pdf_processing_metrics["statement_row_coverage_rate"] == 1
    assert a.pdf_processing_metrics["textract_used"] == 0
    assert all(row.source_regions for row in a.report_transactions)
    assert all(row.source_line_refs for row in parsed.canonical_transactions)


@pytest.mark.parametrize("shuffled", [False, True])
def test_santander_native_uses_cash_summary_and_columns_even_for_payment_named_credits(shuffled, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    a = build_default_conversion_pipeline().run(filename="santander.pdf",
        raw_bytes=positioned_pdf(santander_pages(), shuffled=shuffled), analysis_id="an_santander_columns").analysis_data
    assert [r.amount for r in a.report_transactions] == [23.78, 257.47, 18.75, -300]
    assert a.opening_balance == a.closing_balance == 0
    assert a.total_inflows == 300 and a.total_outflows == -300
    assert a.bank_code == "033"
    assert a.document_type == "bank_statement"
    assert a.pdf_processing_metrics["balance_consistency_failed"] == 0
    assert a.pdf_processing_metrics["financial_controls_validated"] == 1
    assert all(row.source_regions for row in a.report_transactions)


@pytest.mark.parametrize("builder,before,after", [
    (bradesco_pages, "200,00", "201,00"),
    (bradesco_pages, "202,00", "202,0"),
    (bradesco_pages, "Total", "Totais ilegíveis"),
    (bradesco_pages, "PIX ENVIADO", ""),
    (santander_pages, "23,78", "23,79"),
    (santander_pages, "300,00-", "300,0-"),
    (santander_pages, "SALDO EM 28/02", "SALDO ILEGÍVEL"),
    (santander_pages, "(+) Total de Créditos", "Créditos ilegíveis"),
])
def test_incomplete_or_inconsistent_native_cash_columns_cannot_fall_back_to_generic(builder, before, after, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = positioned_pdf(change_cells(builder(), before, after))
    if (before, after) in {("200,00", "201,00"), ("23,78", "23,79")}:
        result = parse_pdf_transactions(raw)
        assert len(result.transactions) == len(parse_pdf_transactions(positioned_pdf(builder())).transactions)
        assert result.parse_metrics["financial_controls_validated"] == 0
        assert "financial_validation_failed" in result.canonical_transactions[0].warnings
    else:
        with pytest.raises(PdfFinancialValidationError):
            parse_pdf_transactions(raw)


def test_offsetting_movements_cannot_disappear_even_when_closing_balance_stays_equal(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = bradesco_pages()
    pages[0] = [cell for cell in pages[0] if cell[1] != 650]
    pages[1] = [(x,y,"200,00" if text == "202,00" and y == 730 else text) for x,y,text in pages[1]]
    with pytest.raises(PdfFinancialValidationError):
        parse_pdf_transactions(positioned_pdf(pages))


@pytest.mark.parametrize("defect", ["second_ledger", "different_account", "opening_date", "credit_sign", "debit_sign", "wrong_counter"])
def test_native_cash_ledger_identity_dates_signs_and_page_sequence_are_verified(defect, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = bradesco_pages()
    if defect == "second_ledger":
        pages += bradesco_pages()
    elif defect == "different_account":
        pages[-1].append((25, 740, "Agência:1234 Conta:9999999-0"))
    elif defect == "opening_date":
        pages = change_cells(pages, "31/08", "01/09")
    elif defect == "credit_sign":
        pages = change_cells(pages, "200,00", "-200,00")
    elif defect == "debit_sign":
        pages[1] = [(x, y, "+202,00" if y == 730 and text == "202,00" else text) for x, y, text in pages[1]]
    else:
        for index, cells in enumerate(pages, 1):
            cells.append((25, 20, f"{index:03}/004"))
    if defect == "wrong_counter":
        result = parse_pdf_transactions(positioned_pdf(pages))
        assert result.parse_metrics["partial_statement"] == 1
        assert result.parse_metrics["financial_controls_validated"] == 0
        return
    with pytest.raises(PdfFinancialValidationError) as caught:
        parse_pdf_transactions(positioned_pdf(pages))
    assert caught.value._parse_observability["ocr_retry_skip_reason"] == "financial_validation_failed"


def test_native_cash_balance_on_a_separate_line_keeps_its_source_evidence(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    parsed = parse_pdf_transactions(positioned_pdf(bradesco_pages()))
    last = parsed.canonical_transactions[-1]
    assert last.running_balance == 1
    assert len(last.source_regions) >= 2 and len(last.source_line_refs) >= 2
