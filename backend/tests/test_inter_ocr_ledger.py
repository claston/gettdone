"""Synthetic Inter OCR: per-movement balances and daily headers across pages."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError, PdfFinancialValidationError


def inter_extraction():
    pages = []

    def page(number, records):
        lines = []
        for text, x, y in records:
            index = len(lines) + 1
            lines.append(ExtractedLine(id=f"p{number}-{index}", page_number=number, line_index=index, text=text,
                bbox={"left":x, "top":y, "width":.09, "height":.008}, confidence=99))
        pages.append(ExtractedPage(page_number=number, lines=lines))

    page(1, [("inter Banco Inter", .07, .03), ("Solicitado em: 08/09/2026 11h08", .6, .03),
        ("CPF/CNPJ: 00.000.000/0001-00, Instituição: Banco Inter, Agência: 0001, Conta: 12345-6", .07, .05),
        ("Período: 01/08/2026 a 31/08/2026", .07, .08),
        ("Saldo disponível:", .2, .12), ("Saldo bloqueado:", .34, .12),
        ("Saldo total", .07, .12), ("R$ 336,30", .07, .15),
        ("1 de Agosto de 2026 Saldo do dia: R$ 1.278,85", .07, .24),
        ("Valor", .73, .24), ("Saldo por transação", .81, .24),
        ('Compra no debito: "LOJA EXEMPLO"', .07, .3), ("-R$ 29,98", .70, .3), ("R$ 1.328,85", .86, .3),
        ('Pix enviado: "PESSOA EXEMPLO"', .07, .36), ("-R$ 50,00", .70, .36), ("R$ 1.278,85", .86, .36),
        ("2 de Agosto de 2026 Saldo do dia: R$ 1.378,85", .07, .44),
        ('Pix recebido: "OUTRA PESSOA"', .07, .5), ("R$ 200,00", .70, .5), ("R$ 1.478,85", .86, .5),
        ("Fale com a gente", .07, .93), ("SAC: 0800 940 9999", .07, .95)])
    page(2, [('Pagamento efetuado: "FORNECEDOR EXEMPLO"', .07, .06), ("-R$ 100,00", .70, .06), ("R$ 1.378,85", .86, .06),
        ("3 de Agosto de 2026 Saldo do dia: $ 1.111,84", .07, .14),
        ('Pix enviado: "OUTRO FORNECEDOR"', .07, .2), ("-R$ 267,01", .70, .2), ("R$ 1.111,84", .86, .2),
        ("Fale com a gente", .07, .93), ("SAC: 0800 940 9999", .07, .95)])
    return RawDocumentExtraction(provider="aws_textract", document_hash="synthetic-inter", pages=pages, metrics={})


def change(extraction, before, after):
    return replace(extraction, pages=[replace(page, lines=[replace(line, text=after) if line.text == before else line
        for line in page.lines]) for page in extraction.pages])


def parse(extraction):
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_daily_headers_and_page_boundary_never_drop_movements_or_become_values():
    extraction = inter_extraction()
    result = parse(extraction)
    assert [row.amount for row in result.transactions] == [-29.98, -50, 200, -100, -267.01]
    assert [row.date for row in result.transactions] == ["2026-08-01"] * 2 + ["2026-08-02"] * 2 + ["2026-08-03"]
    assert [row.running_balance for row in result.canonical_transactions] == [1328.85, 1278.85, 1478.85, 1378.85, 1111.84]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["selected_parser"] == "layout_specific_inter_ocr"
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)
    assert all("Fale com" not in row.description and "SAC" not in row.description for row in result.transactions)
    assert result.source_extraction == extraction


@pytest.mark.parametrize("before,after", [
    ("-R$ 100,00", "-R$ 100,01"), ("R$ 1.478,85", "R$ 1.478,86"),
    ("2 de Agosto de 2026 Saldo do dia: R$ 1.378,85", "2 de Agosto de 2026 Saldo do dia: R$ 1.378,86"),
    ("-R$ 100,00", "R$ 100,00"), ("-R$ 267,01", "R$"),
    ("3 de Agosto de 2026 Saldo do dia: $ 1.111,84", "3 de Setembro de 2026 Saldo do dia: $ 1.111,84"),
])
def test_ambiguous_value_sign_date_or_balance_cannot_fall_back_to_generic(before, after):
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(change(inter_extraction(), before, after))


def test_missing_geometry_is_not_replaced_by_positional_text_guess():
    extraction = inter_extraction()
    page = extraction.pages[1]
    lines = [replace(line, bbox=None) if line.text == "-R$ 100,00" else line for line in page.lines]
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[extraction.pages[0], replace(page, lines=lines)]))


def test_daily_balance_with_missing_currency_decoration_keeps_its_labeled_role():
    extraction = change(inter_extraction(), "3 de Agosto de 2026 Saldo do dia: $ 1.111,84",
                        "3 de Agosto de 2026 Saldo do dia: 1.111,84")
    assert len(parse(extraction).transactions) == 5


def test_unreadable_financial_cell_cannot_be_ignored_when_other_rows_reconcile():
    extraction = inter_extraction()
    page = extraction.pages[0]
    source = next(line for line in page.lines if line.text == "-R$ 50,00")
    extra = replace(source, id="unreadable", text="R$ ilegível", line_index=100,
                    bbox={**source.bbox, "top":.4})
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=[*page.lines, extra]), *extraction.pages[1:]]))


def test_handwritten_balance_is_not_printed_ledger_evidence():
    extraction = inter_extraction()
    page = extraction.pages[0]
    lines = [replace(line, word_ids=("handwritten",)) if line.text == "R$ 1.328,85" else line for line in page.lines]
    word = ExtractedWord(id="handwritten", page_number=1, word_index=1, text="1.328,85", text_type="HANDWRITING")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=lines, words=[word]), *extraction.pages[1:]]))


def test_duplicate_balance_cell_is_not_resolved_by_selecting_one_candidate():
    extraction = inter_extraction()
    page = extraction.pages[0]
    source = next(line for line in page.lines if line.text == "R$ 1.328,85")
    extra = replace(source, id="duplicate", line_index=100)
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=[*page.lines, extra]), *extraction.pages[1:]]))
