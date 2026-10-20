from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError, PdfFinancialValidationError


def santander_extraction(*, damaged=False):
    pages = []

    def page(number, records):
        lines = [
            ExtractedLine(id=f"p{number}-{index}", page_number=number, line_index=index, text=text,
                          bbox={"left": x, "top": y, "width": .065, "height": .008},
                          confidence=65 if damaged and text == "234,56" else 99)
            for index, (text, x, y) in enumerate(records, 1)
        ]
        pages.append(ExtractedPage(page_number=number, lines=lines))

    def header(y):
        return [("Data", .075, y), ("Descrição", .125, y), ("N° Documento", .495, y),
                ("Movimentos (R$)", .64, y), ("Saldo (R$)", .834, y),
                ("Créditos", .637, y + .013), ("Débitos", .717, y + .013)]

    page(1, [("Anotações de conferência: R$ 99.999,99", .1, .10),
             ("Santander Empresas", .1, .20), ("EXTRATO CONSOLIDADO INTELIGENTE", .2, .22),
             ("julho/2026", .8, .24)])
    records = [
        ("Santander Empresas", .1, .02), ("Resumo julho/2026", .1, .08),
        ("Saldo de Conta Corrente em 30/06", .1, .12), ("0,00", .65, .122),
        ("Total de Créditos", .1, .16), ("1.634,72", .62, .162),
        ("Total de Débitos", .1, .20), ("1.634,72", .62, .202),
        ("Saldo de Conta Corrente em 31/07", .1, .24), ("0,00", .65, .242),
        ("Saldo de Investimentos com Resgate Automático", .1, .28), ("2.220,62", .63, .282),
        ("Limite Santander Master", .1, .32), ("1.800,00", .63, .322),
        ("Conta Corrente", .1, .36), ("Movimentação", .1, .38), *header(.40),
        ("SALDO EM 30/06", .125, .44), ("0,00", .86, .442),
        ("01/07", .075, .50), ("PIX ENVIADO", .125, .50), ("100,00", .70, .502),
        ("FORNECEDOR EXEMPLO", .125, .51), ("IOF ADICIONAL AUTOMATICO", .125, .54),
        ("PERIODO: 01/06 A 30/06/26", .125, .55),
        ("RESGATE CONTAMAX AUTOMATICO", .125, .58), ("100,16", .62, .582), ("0,00", .86, .582),
        ("03/07", .075, .72), ("PIX RECEBIDO", .125, .72),
        ("234,56" if damaged else "1.234,56", .62, .722),
        ("CLIENTE EXEMPLO", .125, .73),
        ("APLICACAO CONTAMAX", .125, .76), ("1.234,56-", .70, .762), ("0,00", .86, .762),
    ]
    if not damaged:
        records.append(("0,16-", .72, .542))
    else:
        records.append(("1234,56", .62, .732))  # Off-row annotation, falsely classified as PRINTED by OCR.
    records.sort(key=lambda item: item[2])
    page(2, records)
    page(3, [
        ("Santander Empresas", .1, .02), ("EXTRATO CONSOLIDADO INTELIGENTE", .4, .04),
        ("julho/2026", .8, .06), *header(.10),
        ("08/07", .075, .20), ("PIX RECEBIDO", .125, .20), ("300,00", .62, .202),
        ("CLIENTE EXEMPLO", .125, .21),
        ("APLICACAO CONTAMAX", .125, .24), ("300,00-", .70, .242), ("0,00", .86, .242),
        ("SALDO EM 31/07", .125, .28), ("0,00", .86, .282),
        ("Saldos por Período", .1, .34), ("31/07", .075, .40), ("2.220,62", .70, .402),
        ("Débito Automático em Conta Corrente", .1, .50),
        ("Data", .075, .54), ("Descrição", .14, .54), ("Valor (R$)", .51, .54),
        ("01/07", .075, .58), ("PIX ENVIADO", .14, .58), ("100,00", .54, .582),
    ])
    page(4, [("Investimentos CDB", .1, .1), ("01/07", .075, .2),
             ("APLICACAO CONTAMAX", .125, .2), ("1.234,56", .7, .202)])
    return RawDocumentExtraction(provider="aws_textract", document_hash="synthetic-santander", pages=pages, metrics={})


def change(extraction, before, after, *, page=None):
    return replace(extraction, pages=[replace(item, lines=[replace(line, text=after)
        if line.text == before and (page is None or item.page_number == page) else line for line in item.lines])
        for item in extraction.pages])


def parse(extraction):
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_columns_include_all_movements_and_exclude_repeated_details_and_investments():
    result = parse(santander_extraction())
    assert [row.amount for row in result.transactions] == [-100, -.16, 100.16, 1234.56, -1234.56, 300, -300]
    assert [row.date for row in result.transactions] == ["2026-07-01"] * 3 + ["2026-07-03"] * 2 + ["2026-07-08"] * 2
    assert [row.running_balance for row in result.canonical_transactions] == [None, None, 0, None, 0, None, 0]
    assert [row.source_page for row in result.canonical_transactions] == [2] * 5 + [3] * 2
    assert result.parse_metrics["selected_parser"] == "layout_specific_santander_ocr_columns"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


def test_two_independent_controls_uniquely_restore_missing_fee_and_one_leading_digit_with_review():
    result = parse(santander_extraction(damaged=True))
    assert [row.amount for row in result.transactions] == [-100, -.16, 100.16, 1234.56, -1234.56, 300, -300]
    recovered = [row for row in result.canonical_transactions if row.warnings]
    assert [row.amount for row in recovered] == [-.16, 1234.56]
    assert all("ocr_amount_recovered_from_summary" in row.warnings for row in recovered)
    assert all(len(row.source_line_refs) > 8 for row in recovered)
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["ocr_amounts_recovered_from_summary"] == 2
    assert result.parse_metrics["ocr_off_row_marks_excluded"] == 1


@pytest.mark.parametrize("before,after,page", [
    ("1.634,72", "1.634,73", None),
    ("100,16", "100,17", 2),
    ("234,56", "235,56", 2),
    ("1234,56", "2234,56", 2),
    ("08/07", "08/08", 3),
    ("08/07", "32/07", 3),
    ("SALDO EM 31/07", "SALDO ILEGÍVEL", 3),
    ("Total de Créditos", "Créditos ilegíveis", 2),
    ("IOF ADICIONAL AUTOMATICO", "VALOR ILEGÍVEL", 2),
])
def test_inconsistent_or_ambiguous_document_cannot_fall_back_to_generic(before, after, page):
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(change(santander_extraction(damaged=True), before, after, page=page))


def test_missing_geometry_cannot_silently_drop_a_financial_cell():
    extraction = santander_extraction()
    extraction = replace(extraction, pages=[replace(page, lines=[replace(line, bbox=None)
        if line.text == "100,16" else line for line in page.lines]) for page in extraction.pages])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)


def test_high_confidence_printed_amount_cannot_be_changed_to_match_a_balance():
    extraction = santander_extraction(damaged=True)
    extraction = replace(extraction, pages=[replace(page, lines=[replace(line, confidence=99)
        if line.text == "234,56" else line for line in page.lines]) for page in extraction.pages])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)


def test_both_decimal_punctuation_variants_remain_confined_to_financial_columns():
    result = parse(change(santander_extraction(), "100,00", "100.00", page=2))
    assert result.transactions[0].amount == -100
    assert result.transactions[1].date == "2026-07-01"


def test_extra_unassigned_money_is_refused_even_when_the_ledger_already_closes():
    extraction = santander_extraction()
    page = extraction.pages[1]
    extra = replace(page.lines[-1], id="extra", line_index=999, text="1,00", bbox={"left": .62, "top": .735, "width": .05, "height": .008})
    extraction = replace(extraction, pages=[extraction.pages[0], replace(page, lines=[*page.lines, extra]), *extraction.pages[2:]])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)


def test_two_possible_leading_digit_repairs_are_ambiguous_even_if_either_closes_the_controls():
    extraction = change(santander_extraction(damaged=True), "1.234,56-", "1.469,12-")
    extraction = change(extraction, "1.634,72", "1.869,28")
    page = extraction.pages[1]
    anchor = replace(page.lines[-1], id="second-pix", line_index=990, text="PIX RECEBIDO",
                     bbox={"left": .125, "top": .74, "width": .10, "height": .008})
    amount = replace(anchor, id="second-amount", line_index=991, text="234,56", confidence=65,
                     bbox={"left": .62, "top": .742, "width": .07, "height": .008})
    extraction = replace(extraction, pages=[extraction.pages[0], replace(page, lines=[*page.lines, anchor, amount]),
                                           *extraction.pages[2:]])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)


def test_handwritten_amount_is_not_authoritative_even_when_all_financial_controls_close():
    extraction = santander_extraction()
    page = extraction.pages[1]
    source = next(line for line in page.lines if line.text == "100,00")
    word = ExtractedWord(id="handwritten", page_number=2, word_index=1, text=source.text,
                         bbox=source.bbox, text_type="HANDWRITING", confidence=99)
    page = replace(page, words=[word], lines=[replace(line, word_ids=(word.id,)) if line.id == source.id else line
                                            for line in page.lines])
    extraction = replace(extraction, pages=[extraction.pages[0], page, *extraction.pages[2:]])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)


def test_nonzero_cash_balance_is_preserved_independently_of_available_balance():
    result = parse(change(santander_extraction(), "0,00", "100,00"))
    from app.application.default_conversion_pipeline import _resolve_opening_balance

    assert _resolve_opening_balance([], extracted_text=result.extracted_text, layout_name=result.layout.layout_name) == 100
    assert result.canonical_transactions[-1].running_balance == 100


def test_missing_fee_requires_an_independent_daily_closing_balance():
    extraction = santander_extraction(damaged=True)
    page = extraction.pages[1]
    page = replace(page, lines=[line for line in page.lines if not
                               (line.text == "0,00" and abs(line.bbox["top"] - .582) < .001)])
    extraction = replace(extraction, pages=[extraction.pages[0], page, *extraction.pages[2:]])
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(extraction)
