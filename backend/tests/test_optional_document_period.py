"""A missing document range must not veto independently dated cash movements."""

import re
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_final_recovery_native import english_statement
from tests.test_oct27_priority_recovery import detail_statement
from tests.test_recovery_case_fixes import mercado_pago_page


@pytest.mark.parametrize("kind", ["mercado_pago", "bb_english", "account_detail"])
def test_missing_document_period_preserves_all_independent_dates_values_and_signs(kind, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind == "mercado_pago":
        text = mercado_pago_page()
        raw = generate_native_text_pdf((text.splitlines(),))
        missing = generate_native_text_pdf((text.replace("Periodo: De 01-07-2026 al 31-07-2026\n", "").splitlines(),))
    elif kind == "bb_english":
        text = english_statement()
        raw = generate_native_text_pdf((text.splitlines(),))
        missing = generate_native_text_pdf((text.replace("Period 09/28/2026 - 10/15/2026\n", "").splitlines(),))
    else:
        raw = detail_statement()
        # The fixture can remove just the range while keeping its structural summary.
        missing = detail_statement(mutation="missing_period")
    expected = pdf_parser.parse_pdf_transactions(raw)
    result = pdf_parser.parse_pdf_transactions(missing)
    assert [(r.date, r.amount) for r in result.transactions] == [(r.date, r.amount) for r in expected.transactions]
    assert result.parse_metrics["document_period_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["statement_validation_failed"] == 0
    assert "document_period_unavailable" in result.canonical_transactions[0].warnings


def test_optional_period_is_shared_and_does_not_fabricate_bounds_or_date_context():
    from app.application.parsers.pdf.document_period import optional_document_period, period_contains, require_period_context
    from app.application.parsers.pdf.statement_review import statement_review_scope

    with statement_review_scope():
        assert optional_document_period(None) == (None, None)
        assert period_contains("2026-09-02", None, None)
        with pytest.raises(InvalidFileContentError):
            require_period_context(None, None)
        with pytest.raises(InvalidFileContentError):
            period_contains("02/09", None, None)


def test_conflicting_or_invalid_printed_period_is_not_treated_as_missing():
    from app.application.parsers.pdf.document_period import optional_document_period, unique_document_period
    from app.application.parsers.pdf.statement_review import statement_review_scope

    with statement_review_scope():
        with pytest.raises(InvalidFileContentError):
            unique_document_period([("01/09/2026", "30/09/2026"), ("01/10/2026", "31/10/2026")])
        with pytest.raises(InvalidFileContentError):
            optional_document_period(re.search(r"(.+) a (.+)", "30/09/2026 a 01/09/2026"))


def _without_period(source):
    return replace(source, pages=[replace(page, lines=[replace(line, text=re.sub(
        r"( de) \d{2}/\d{2}/\d{4} a \d{2}/\d{2}/\d{4}", r"\1", line.text, flags=re.I)) for line in page.lines
        if not re.search(r"per[ií]odo.*\d|movimenta[çc][ãa]o em|lan[çc]amentos de\s+\d", line.text, re.I)])
        for page in source.pages])


@pytest.mark.parametrize("kind", ["sicredi", "bb", "inter", "uniprime", "mercado_pago", "caixa"])
def test_ocr_models_share_period_review_without_losing_geometry_or_transactions(kind):
    from tests.test_final_recovery_ocr import bb_combined, caixa_page, sicredi
    from tests.test_inter_ocr_ledger import inter_extraction
    from tests.test_oct27_today_ocr_controls import mercado
    from tests.test_recovery_case_ocr import uniprime_source
    from tests.test_recovery_sweep_ocr import extraction

    factories = {"sicredi": sicredi, "bb": bb_combined, "inter": inter_extraction,
                 "uniprime": uniprime_source, "mercado_pago": lambda: mercado(annotated=False),
                 "caixa": lambda: replace(extraction([]), pages=[
                     caixa_page("01/01/2024", "31/01/2024", "02/01/2024", "100.00", "150.00")])}
    source = factories[kind]()
    expected = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(_without_period(source))
    assert [(r.date, r.amount) for r in result.transactions] == [(r.date, r.amount) for r in expected.transactions]
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)
    assert result.parse_metrics["document_period_unavailable"] == 1
    assert result.parse_metrics["export_recommendation_reason"] == "document_period_unavailable"
    assert result.parse_metrics["statement_validation_failed"] == 0


@pytest.mark.parametrize("currency", ["BRL", "USD"])
def test_wise_missing_coverage_preserves_textual_dates_and_currency(currency, monkeypatch):
    from tests.test_wise_statements import wise_lines

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    lines = wise_lines(currency)
    lines = [line for line in lines if " - " not in line or "[GMT" not in line]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((lines,)))
    assert [row.amount for row in result.transactions] == [40, -40, 900, 450, 30, -1378.50]
    assert result.parse_metrics["document_period_unavailable"] == 1


def test_bradesco_fully_dated_rows_do_not_require_period_but_short_dates_still_do(monkeypatch):
    from tests.fixtures.positioned_cash_statements import bradesco_pages, positioned_pdf

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    def missing(kind):
        return positioned_pdf([[cell for cell in page if not cell[2].startswith("Período ")]
                               for page in bradesco_pages(kind)])
    result = pdf_parser.parse_pdf_transactions(missing("movements"))
    assert [row.amount for row in result.transactions] == [2, 200, -202]
    assert result.parse_metrics["document_period_unavailable"] == 1
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(missing("unified"))


@pytest.mark.parametrize("token,replacement", [("-R$ 100,00", "R$"), ("-R$ 100,00", "R$ 100,00")])
def test_optional_period_never_relaxes_unreadable_values_or_signs(token, replacement):
    from tests.test_inter_ocr_ledger import change, inter_extraction

    source = _without_period(change(inter_extraction(), token, replacement))
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


def test_missing_period_warning_is_scoped_to_its_document(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    text = mercado_pago_page()
    missing = text.replace("Periodo: De 01-07-2026 al 31-07-2026\n", "")
    pdf_parser.parse_pdf_transactions(generate_native_text_pdf((missing.splitlines(),)))
    complete = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((text.splitlines(),)))
    assert not complete.parse_metrics.get("document_period_unavailable")
    assert all("document_period_unavailable" not in row.warnings for row in complete.canonical_transactions)


@pytest.mark.parametrize("kind", ["btg", "banrisul_portal", "sales", "nubank_rdb"])
def test_financial_and_native_models_do_not_fabricate_document_coverage(kind, monkeypatch):
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.statement_period_history import statement_period_from_analysis
    from tests.test_btg_summary_statement import btg_summary_pages
    from tests.test_final_recovery_sales import sales_report
    from tests.test_nubank_rdb_financial_report import rdb_pages
    from tests.test_oct20_followup_native import portal_pages

    pages = {"btg": btg_summary_pages, "banrisul_portal": portal_pages,
             "sales": lambda: [sales_report()], "nubank_rdb": rdb_pages}[kind]()
    missing = ["\n".join(line for line in page.splitlines() if not re.match(r"per[ií]odo\b", line, re.I)) for page in pages]
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(page.splitlines() for page in missing))
    expected = pdf_parser.parse_pdf_transactions(generate_native_text_pdf(tuple(page.splitlines() for page in pages)))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [(r.date, r.amount) for r in result.transactions] == [(r.date, r.amount) for r in expected.transactions]
    assert result.parse_metrics["document_period_unavailable"] == 1
    data = build_default_conversion_pipeline().run(filename="period.pdf", raw_bytes=raw,
        analysis_id="an_missing_period", pdf_parser=lambda *a, **kw: result).analysis_data
    assert data.pdf_processing_metrics["document_period_unavailable"] == 1
    if data.financial_document:
        assert data.financial_document["metadata"].get("period_start") is None
        assert data.financial_document["metadata"].get("period_end") is None
        if kind == "nubank_rdb":
            closing = next(item for item in data.financial_document["observations"] if item["observation_type"] == "closing_position")
            assert closing["dates"] == {}
    period = statement_period_from_analysis(data)
    assert period is None or period["basis"] == "movement_dates"
