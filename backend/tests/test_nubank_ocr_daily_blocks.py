"""Synthetic Nubank OCR pages with independent daily and statement controls."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError, PdfFinancialValidationError


def nubank_extraction():
    pages = []

    def page(number, records):
        lines = []
        for text, x, y in records:
            index = len(lines) + 1
            lines.append(ExtractedLine(id=f"p{number}-{index}", page_number=number, line_index=index, text=text,
                bbox={"left": x, "top": y, "width": .09, "height": .008}, confidence=99))
        pages.append(ExtractedPage(page_number=number, lines=lines))

    header = [("Nubank", .1, .02), ("01 DE SETEMBRO DE 2026 a 30 DE SETEMBRO DE 2026", .1, .05),
              ("VALORES EM R$", .1, .08)]
    page(1, [*header, ("Saldo inicial", .2, .1), ("0,00", .84, .1),
        ("Saldo final do período", .2, .12), ("2.821,35", .84, .12),
        ("Total de entradas", .2, .14), ("+3.557,10", .84, .14),
        ("Total de saídas", .2, .16), ("-735,75", .84, .16),
        ("Movimentações", .1, .2), ("11 SET 2026", .1, .24),
        ("Total de entradas", .2, .24), ("+2.457,10", .84, .24),
        ("Pagamento Recebido", .2, .28), ("PESSOA EXEMPLO", .43, .28), ("1.234,74", .84, .28),
        ("Pagamento Recebido", .2, .34), ("PESSOA EXEMPLO", .43, .34), ("1.222,36", .84, .34),
        ("Saldo do dia", .2, .4), ("2.457,10", .84, .4),
        ("14 SET 2026", .1, .46), ("Total de entradas", .2, .46), ("+1.100,00", .84, .46),
        ("Transferência recebida pelo Pix", .2, .5), ("1.100,00", .84, .5),
        ("Total de saídas", .2, .56), ("-735,75", .84, .56),
        ("Transferência enviada pelo Pix", .2, .6), ("400,00", .84, .6),
        ("Tem alguma dúvida? Atendimento Nubank", .1, .93)])
    page(2, [*header, ("BANCO EXEMPLO Agência: 9999 Conta: 123456-7", .43, .22),
        ("Pagamento de boleto efetuado", .2, .3), ("335,75", .84, .3),
        ("Tem alguma dúvida? Atendimento Nubank", .1, .93)])
    page(3, [*header, ("Saldo do dia", .2, .24), ("2.821,35", .84, .24),
        ("Tem alguma dúvida? Atendimento Nubank", .1, .93)])
    return RawDocumentExtraction(provider="aws_textract", document_hash="synthetic-nubank", pages=pages, metrics={})


def change_text(extraction, before, after, *, occurrence=0):
    seen = 0
    pages = []
    for page in extraction.pages:
        lines = []
        for line in page.lines:
            if line.text == before:
                if seen == occurrence:
                    line = replace(line, text=after)
                seen += 1
            lines.append(line)
        pages.append(replace(page, lines=lines))
    return replace(extraction, pages=pages)


def parse(extraction):
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_receipts_are_not_omitted_and_daily_totals_never_become_transaction_balances():
    result = parse(nubank_extraction())
    assert [row.amount for row in result.transactions] == [1234.74, 1222.36, 1100, -400, -335.75]
    assert [row.running_balance for row in result.canonical_transactions] == [None, 2457.1, None, None, 2821.35]
    assert [row.date for row in result.transactions] == ["2026-09-11"] * 2 + ["2026-09-14"] * 3
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["selected_parser"] == "layout_specific_nubank_ocr"
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)
    assert "Agência" in result.transactions[3].description
    assert "Nubank" not in result.transactions[3].description


@pytest.mark.parametrize("before,after,occurrence", [
    ("1.234,74", "1.234,75", 0),
    ("+2.457,10", "+2.457,11", 0),
    ("2.457,10", "2.457,11", 0),
    ("2.821,35", "2.821,36", 0),
    ("335,75", "335,74", 0),
    ("14 SET 2026", "14 OUT 2026", 0),
    ("Pagamento Recebido", "Saldo do dia", 0),
])
def test_inconsistent_daily_or_statement_controls_cannot_fall_back_to_generic(before, after, occurrence):
    extraction = change_text(nubank_extraction(), before, after, occurrence=occurrence)
    with pytest.raises((PdfFinancialValidationError, OcrFinancialAmbiguityError)):
        parse(extraction)


def test_amount_at_a_different_vertical_position_is_not_assigned_to_a_movement():
    extraction = nubank_extraction()
    page = extraction.pages[0]
    lines = [replace(line, bbox={**line.bbox, "top": .31}) if line.text == "1.234,74" else line for line in page.lines]
    extraction = replace(extraction, pages=[replace(page, lines=lines), *extraction.pages[1:]])
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


def test_reversal_in_entry_section_remains_a_credit_despite_enviada_in_description():
    extraction = change_text(nubank_extraction(), "Pagamento Recebido", "Estorno Transferência enviada pelo Pix")
    result = parse(extraction)
    assert result.transactions[0].amount == 1234.74
    assert result.source_extraction == extraction


@pytest.mark.parametrize("raw", ["1.234,7?", "valor ilegível"])
def test_illegible_amount_is_not_filled_from_daily_total(raw):
    with pytest.raises((OcrFinancialAmbiguityError, PdfFinancialValidationError)):
        parse(change_text(nubank_extraction(), "1.234,74", raw))


def test_handwritten_value_is_not_used_to_validate_printed_statement():
    extraction = nubank_extraction()
    page = extraction.pages[0]
    lines = [replace(line, word_ids=("handwritten",)) if line.text == "1.234,74" else line for line in page.lines]
    word = ExtractedWord(id="handwritten", page_number=1, word_index=1, text="1.234,74", text_type="HANDWRITING")
    extraction = replace(extraction, pages=[replace(page, lines=lines, words=[word]), *extraction.pages[1:]])
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


def test_duplicate_value_for_same_movement_is_not_silently_deduplicated():
    extraction = nubank_extraction()
    page = extraction.pages[0]
    value = next(line for line in page.lines if line.text == "1.234,74")
    lines = list(page.lines)
    lines.insert(lines.index(value) + 1, replace(value, id="duplicate"))
    lines = [replace(line, line_index=index + 1) for index, line in enumerate(lines)]
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=lines), *extraction.pages[1:]]))


def test_missing_geometry_does_not_fall_back_to_ambiguous_text_parser():
    extraction = nubank_extraction()
    page = extraction.pages[0]
    lines = [replace(line, bbox=None) if line.text == "1.234,74" else line for line in page.lines]
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=lines), *extraction.pages[1:]]))


def test_explicit_credit_sign_in_debit_section_is_a_financial_conflict():
    with pytest.raises(PdfFinancialValidationError):
        parse(change_text(nubank_extraction(), "400,00", "+400,00"))
