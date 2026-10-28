"""Multi-month documents retain all movements and independently checked balances."""

import calendar
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError, UnsupportedDocumentContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def bb_month(month, *, year=2025, opening=1000, pages=1, account="12345-6", header=True):
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    result = []
    running = opening
    for index in range(pages):
        lines = ["Banco do Brasil", "Extrato de Conta Corrente", "Cliente: EMPRESA FICTÍCIA",
                 f"Agência: 1234-5 Conta: {account}"]
        if header:
            lines.append(f"Período: 01 a {end:%d/%m/%Y}")
        lines.append("Dia Lote Documento Histórico Valor")
        if index == 0:
            lines.append(f"{start - timedelta(days=1):%d/%m/%Y} Saldo Anterior {opening:.2f} (+)".replace(".", ","))
        day = start + timedelta(days=index)
        lines += [f"{day:%d/%m/%Y} 14397 {index+1:06d} PIX RECEBIDO 50,00 (+)",
                  f"{day:%d/%m/%Y} 13105 {index+101:06d} PIX ENVIADO 20,00 (-)"]
        running += 30
        lines.append(f"{day:%d/%m/%Y} Saldo do dia {running:.2f} (+)".replace(".", ","))
        if index == pages - 1:
            lines.append(f"{end:%d/%m/%Y} S A L D O {running:.2f} (+)".replace(".", ","))
        lines.append(f"Página: {index+1}/{pages}")
        result.append("\n".join(lines))
    return result


@pytest.fixture(autouse=True)
def no_remote_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def pdf(pages):
    return generate_native_text_pdf(tuple(page.splitlines() for page in pages))


def test_bb_72_pages_twelve_months_are_one_conversion_with_all_original_pages():
    pages = [page for month in range(1, 13) for page in bb_month(month, opening=1000+(month-1)*180, pages=6)]
    result = pdf_parser.parse_pdf_transactions(pdf(pages))
    assert len(result.transactions) == 144
    assert [r.amount for r in result.transactions] == [50, -20] * 72
    assert {r.date[:7] for r in result.transactions} == {f"2025-{month:02d}" for month in range(1, 13)}
    assert {r.source_page for r in result.canonical_transactions} == set(range(1, 73))
    assert [r.source_sequence for r in result.canonical_transactions] == list(range(1, 145))
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert not result.parse_metrics.get("partial_statement")
    assert len(result.document_parts) == 12
    data = build_default_conversion_pipeline().run(filename="year.pdf", raw_bytes=pdf(pages),
        analysis_id="an_multi_month", pdf_parser=lambda *a, **kw: result).analysis_data
    assert data.transactions_total == 144
    assert data.opening_balance == 1000
    assert data.closing_balance == 3160
    from app.application.statement_period_history import statement_period_from_analysis

    assert statement_period_from_analysis(data) == {
        "start_date": "2025-01-01", "end_date": "2025-12-31", "basis": "document_metadata"}


@pytest.mark.parametrize("months,opening", [([1, 2], 999), ([1, 3], 1030), ([2, 1], 1030)])
def test_independent_blocks_with_gaps_balance_differences_or_reversed_order_preserve_rows(months, opening):
    pages = bb_month(months[0]) + bb_month(months[1], opening=opening)
    result = pdf_parser.parse_pdf_transactions(pdf(pages))
    assert [r.amount for r in result.transactions] == [50, -20, 50, -20]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    data = build_default_conversion_pipeline().run(filename="blocks.pdf", raw_bytes=pdf(pages),
        analysis_id="an_blocks", pdf_parser=lambda *a, **kw: result).analysis_data
    assert data.opening_balance is data.closing_balance is None
    assert data.pdf_processing_metrics["export_recommendation"] == "review_recommended"
    from app.application.statement_period_history import statement_period_from_analysis

    if months != [2, 1]:
        period = statement_period_from_analysis(data)
        assert period["basis"] == ("movement_dates" if months == [1, 3] else "document_metadata")


def test_missing_headers_do_not_prevent_identifying_independently_dated_openings():
    result = pdf_parser.parse_pdf_transactions(pdf(bb_month(1, header=False)+bb_month(2, opening=1030, header=False)))
    assert [r.amount for r in result.transactions] == [50, -20, 50, -20]
    assert {r.date[:7] for r in result.transactions} == {"2025-01", "2025-02"}


def test_a_later_invalid_block_is_not_silently_dropped():
    pages = bb_month(1) + [page.replace("20,00 (-)", "valor ilegível (-)") for page in bb_month(2, opening=1030)]
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(pdf(pages))


def test_legitimate_repeated_transactions_are_preserved_and_duplicates_can_still_be_reviewed():
    first = bb_month(1)[0]
    movement = "01/01/2025 14397 000001 PIX RECEBIDO 50,00 (+)"
    first = first.replace(movement, movement+"\n"+movement).replace("1030,00", "1080,00")
    raw = pdf([first, *bb_month(2, opening=1080)])
    data = build_default_conversion_pipeline().run(filename="duplicates.pdf", raw_bytes=raw,
        analysis_id="an_repeated_movements").analysis_data
    assert data.transactions_total == 5
    assert [row.amount for row in data.report_transactions] == [50, 50, -20, 50, -20]
    assert data.potential_duplicates >= 1
    assert data.pdf_processing_metrics["balance_consistency_failed"] == 0


def test_months_are_not_used_as_account_identity():
    from app.application.pdf_document_segmentation import segment_pdf_pages
    from tests.test_pdf_statement_identity import statement_page

    first, second = statement_page(), statement_page(month="out")
    assert segment_pdf_pages([first, second]).page_texts == (first, second)
    with pytest.raises(UnsupportedDocumentContentError):
        segment_pdf_pages([first, statement_page(month="out", owner_account="987654321-0")])


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_shared_block_processing_also_handles_mercado_pago(representation):
    from tests.test_oct27_today_ocr_controls import mercado
    from tests.test_recovery_case_fixes import mercado_pago_page

    if representation == "native":
        first = mercado_pago_page()
        second = first.replace("07-2026", "08-2026")
        result = pdf_parser.parse_pdf_transactions(pdf([first, second]))
        assert [r.amount for r in result.transactions] == [50, -20, 20] * 2
    else:
        source = mercado(annotated=False)
        second = replace(source.pages[0], page_number=2,
            lines=[replace(line, id="p2-"+line.id, page_number=2, text=line.text.replace("08-2026", "07-2026"))
                   for line in source.pages[0].lines])
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[source.pages[0], second]))
        assert [r.amount for r in result.transactions] == [50, -20] * 2
        assert [r.source_page for r in result.canonical_transactions] == [1, 1, 2, 2]
    assert len(result.document_parts) == 2
