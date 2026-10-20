from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError, PdfFinancialValidationError


def ailos_extraction():
    pages = []

    def page(number, records):
        lines = []
        for text, x, y in records:
            index = len(lines) + 1
            lines.append(
                ExtractedLine(
                    id=f"p{number}-{index}",
                    page_number=number,
                    line_index=index,
                    text=text,
                    bbox={"left": x, "top": y, "width": 0.07, "height": 0.008},
                    confidence=99,
                )
            )
        pages.append(ExtractedPage(page_number=number, lines=lines))

    header = [
        ("DATA", 0.03, 0.13),
        ("DESCRIÇÃO", 0.126, 0.13),
        ("DOCUMENTO", 0.516, 0.13),
        ("CRÉDITO (R$)", 0.621, 0.13),
        ("DÉBITO (R$)", 0.76, 0.13),
        ("SALDO (R$)", 0.895, 0.13),
    ]
    page(
        1,
        [
            ("AILOS", 0.136, 0.04),
            ("Período 01/08/2026 a 31/08/2026", 0.37, 0.06),
            ("Cooperativa: CIVIA Banco: 085 Agência: 0001 Conta: 12345", 0.28, 0.10),
            *header,
            ("SALDO ANTERIOR", 0.126, 0.17),
            ("1.000,00", 0.9, 0.17),
            ("03/08/2026", 0.03, 0.23),
            ("CREDITO PIX CLIENTE EXEMPLO", 0.126, 0.23),
            ("123456.123", 0.51, 0.23),
            ("100,00", 0.65, 0.23),
            ("1.100,00", 0.9, 0.23),
            ("03/08/2026", 0.03, 0.30),
            ("DEBITO PIX FORNECEDOR EXEMPLO", 0.126, 0.30),
            ("123456.456", 0.51, 0.30),
            ("-50,00", 0.79, 0.30),
            ("1.050,00", 0.9, 0.30),
        ],
    )
    page(
        2,
        [
            *header,
            ("04/08/2026 TEDELETRONICO", 0.03, 0.23),
            ("123456.789", 0.51, 0.23),
            ("-10,00", 0.80, 0.23),
            ("1.040,00", 0.9, 0.23),
            ("TOTAL", 0.03, 0.30),
            ("100,00", 0.65, 0.30),
            ("-60,00", 0.79, 0.30),
            ("1.040,00", 0.9, 0.30),
            ("Os dados acima têm como base 02/09/2026", 0.03, 0.4),
        ],
    )
    return RawDocumentExtraction(provider="aws_textract", document_hash="synthetic-ailos", pages=pages, metrics={})


def change(extraction, before, after):
    return replace(
        extraction,
        pages=[
            replace(page, lines=[replace(line, text=after) if line.text == before else line for line in page.lines])
            for page in extraction.pages
        ],
    )


def parse(extraction):
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_ailos_financial_columns_keep_all_movements_and_independent_controls():
    result = parse(ailos_extraction())
    assert [row.amount for row in result.transactions] == [100, -50, -10]
    assert [row.running_balance for row in result.canonical_transactions] == [1100, 1050, 1040]
    assert [row.date for row in result.transactions] == ["2026-08-03", "2026-08-03", "2026-08-04"]
    assert result.parse_metrics["selected_parser"] == "layout_specific_ailos_ocr"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


@pytest.mark.parametrize(
    "before,after",
    [
        ("-50,00", "-50,01"),
        ("1.050,00", "1.050,01"),
        ("-60,00", "-60,01"),
        ("1.000,00", "1.000,01"),
        ("04/08/2026 TEDELETRONICO", "04/09/2026 TEDELETRONICO"),
        ("03/08/2026", "©3/08/2026"),
    ],
)
def test_ailos_unconfirmed_dates_values_or_summary_are_not_corrected_by_balance_difference(before, after):
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(change(ailos_extraction(), before, after))


def test_missing_opening_label_cannot_bypass_specific_financial_validation():
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(change(ailos_extraction(), "SALDO ANTERIOR", "SALDO ILEGÍVEL"))
