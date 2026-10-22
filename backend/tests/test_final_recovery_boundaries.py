"""Adversarial coverage for OCR repair, column drift and document boundaries."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from tests.test_final_recovery_ocr import bb_combined, caixa_page, sicredi
from tests.test_oct20_followup_ocr import bradesco
from tests.test_recovery_sweep_ocr import extraction


@pytest.mark.parametrize("direction", ["50,00D D", "50,00D C", "50,00"])
def test_bb_repeated_sign_requires_agreement_and_never_infers_missing_sign(direction):
    source = bb_combined()
    page = source.pages[0]
    lines = [replace(line, text=direction) if line.text == "50,00D" else
             replace(line, text="832721100156300", bbox={**line.bbox, "left": .636}) if line.text == "100001"
             else line for line in page.lines]
    source = replace(source, pages=[replace(page, lines=lines)])
    if direction == "50,00D D":
        result = pdf_parser._parse_textract_text_pages(source)
        assert result.transactions[0].amount == -50
        assert result.canonical_transactions[0].external_reference_id == "832721100156300"
    else:
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_textract_text_pages(source)


@pytest.mark.parametrize("text_type", ["PRINTED", "HANDWRITING"])
def test_sicredi_balance_glyph_repair_requires_printed_evidence_and_complete_ledger(text_type):
    source = sicredi()
    page = source.pages[0]
    # Replace the synthetic credit and both its printed balance and the next
    # balance. The OCR Z/2 ambiguity exists only in the first balance column.
    mapping = {"50.000,00": "2.000,00", "49.900,00": "1.900,00"}
    lines = [replace(line, text=mapping.get(line.text, line.text)) for line in page.lines]
    balance = next(line for line in lines if line.text == "2.000,00" and line.bbox["left"] > .8)
    word = ExtractedWord(id="balance-word", page_number=1, word_index=1, text="Z.000,00",
                         bbox=balance.bbox, text_type=text_type)
    lines = [replace(line, text="Z.000,00", word_ids=(word.id,)) if line.id == balance.id else line for line in lines]
    source = replace(source, pages=[replace(page, lines=lines, words=[word])])
    if text_type == "PRINTED":
        result = pdf_parser._parse_textract_text_pages(source)
        assert result.parse_metrics["balance_consistency_failed"] == 0
        assert result.parse_metrics["export_recommendation"] == "review_recommended"
        assert "ocr_balance_recovered_from_context" in result.canonical_transactions[0].warnings
        assert source.pages[0].words[0].text == "Z.000,00"
        broken = [replace(line, text="2.001,00") if line.text == "2.000,00" else line for line in lines]
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=broken, words=[word])]))
    else:
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_textract_text_pages(source)


def test_sicredi_headerless_continuation_keeps_column_shift_and_all_balances():
    first = sicredi().pages[0]
    first = replace(first, lines=[line for line in first.lines if line.bbox["top"] < .3])
    second = extraction([
        ("03/09/2026", .08, .2), ("PAGAMENTO TESTE", .18, .2), ("100003", .58, .2),
        ("-100,00", .73, .2), ("49.800,00", .86, .2),
        ("Lançamentos Futuros", .08, .3),
    ], page=2).pages[0]
    source = replace(sicredi(), pages=[first, second])
    result = pdf_parser._parse_textract_text_pages(source)
    assert len(result.transactions) == 3
    assert result.parse_metrics["verified_closing_balance"] == 49800


def test_bradesco_complete_pages_decimal_dot_and_us_thousands_keep_validation():
    source = bradesco()
    pages = []
    for page in source.pages:
        lines = [replace(line, text=line.text.replace("/10", "/2")) if line.text.startswith("Folha") else
                 replace(line, text={"100,00": "1,100.00", "150,00": "1,150.00", "130,00": "1,130.00",
                                    "50,00": "50.00", "20,00": "20.00"}.get(line.text, line.text)) for line in page.lines]
        pages.append(replace(page, lines=lines))
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=pages))
    assert result.parse_metrics["partial_statement"] == 0
    assert result.parse_metrics["verified_opening_balance"] == 1100
    assert result.parse_metrics["verified_closing_balance"] == 1130
    assert all(row.source_line_refs for row in result.canonical_transactions)


def test_caixa_signed_amounts_and_daily_controls_survive_scan_slope():
    page = caixa_page("01/01/2024", "31/01/2024", "02/01/2024", "-20.00", "980.00")
    # A slanted scan moves far-right numeric cells above the date baseline.
    lines = [replace(line, bbox={**line.bbox, "top": line.bbox["top"]-.02*line.bbox["left"]})
             if line.bbox["top"] >= .2 else line for line in page.lines]
    source = replace(extraction([]), pages=[replace(page, lines=lines)])
    result = pdf_parser._parse_textract_text_pages(source)
    assert len(result.transactions) == 1 and result.transactions[0].amount == -20
    assert result.parse_metrics["balance_consistency_failed"] == 0
    bad = [replace(line, text="981.00") if line.text == "980.00" and line.bbox["top"] > .22 else line for line in lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=bad)]))
