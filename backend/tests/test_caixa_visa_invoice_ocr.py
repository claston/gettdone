from dataclasses import replace
from unittest.mock import patch

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from tests.test_oct20_followup_ocr import ocr


def caixa_invoice():
    return ocr([[
        ("cartões CAIXA VISA", .1, .02), ("VENCIMENTO", .80, .05), ("15/10/2026", .81, .07),
        ("VALOR TOTAL DESTA FATURA", .57, .10), ("R$ 265,00", .68, .13),
        ("LIMITE TOTAL", .1, .16), ("R$ 50.000,00", .40, .16),
        ("PARCELAMENTO DE FATURA", .1, .36), ("R$ 863,23", .2, .40),
    ], [
        ("Saldo Anterior", .1, .10), ("226", .45, .10),
        ("Demonstrativo", .51, .15), ("Data", .52, .17), ("Descrição", .56, .17),
        ("Cidade/País", .72, .17),
        ("Crédito/Débito", .90, .17),
        ("07/09", .52, .19), ("TOTAL DA FATURA ANTERIOR", .56, .19), ("100,00D", .92, .19),
        ("15/09", .52, .21), ("OBRIGADO PELO PAGAMENTO", .56, .21), ("100,00C", .92, .21),
        ("05/10", .52, .23), ("CASHBACK ANUIDADE", .56, .23), ("5,00C", .92, .23),
        ("Total", .52, .25), ("5,00", .92, .25),
        ("CLIENTE EXEMPLO (Cartão 1111)", .51, .27), ("ANUIDADE", .51, .29),
        ("Crédito/Débito R$", .90, .30), ("ANUIDADE NACIONAL ADIC PJ 04/12", .51, .31), ("10,00D", .92, .31),
        ("COMPRAS (Cartão 1111)", .51, .34),
        ("Data", .52, .35), ("Descrição", .56, .35), ("Crédito/Débito", .90, .35),
        ("17/09", .53, .37), ("LOJA EXEMPLO", .56, .37), ("SOROCABA", .72, .37), ("200,00D", .92, .37),
        ("Total COMPRAS", .52, .39), ("200,00D", .92, .39),
        ("COMPRAS PARCELADAS (Cartão 1111)", .51, .42),
        ("Data", .52, .43), ("Descrição", .56, .43), ("Crédito/Débito", .90, .43),
        ("16/07", .53, .45), ("LOJA PARCELADA 03 DE 03", .56, .45), ("60.00D", .92, .45),
        ("Total COMPRAS PARCELADAS", .52, .47), ("60,00D", .92, .47),
        ("Total final (cartão 1111)", .52, .49), ("270,00D", .92, .49),
        ("Valor total desta fatura R$ 265,00 D", .60, .52),
        ("CET ROTATIVO", .1, .55), ("318,50% a.a", .45, .55),
    ]])


def modify(extraction, before, after=None, *, missing_bbox=False):
    return replace(extraction, pages=[replace(page, lines=[
        replace(line, text=after if after is not None else line.text, bbox=None if missing_bbox else line.bbox)
        if line.text == before else line for line in page.lines]) for page in extraction.pages])


def test_caixa_invoice_uses_financial_sections_and_signed_amounts_not_limits_or_simulations():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_invoice())
    assert [r.amount for r in result.transactions] == [100, 5, -10, -200, -60]
    assert [r.date for r in result.transactions] == ["2026-09-15", "2026-10-05", "2026-10-15", "2026-09-17", "2026-07-16"]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -265
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    fee = result.canonical_transactions[2]
    assert "invoice_due_date_used_for_undated_fee" in fee.warnings
    assert "sem data" in fee.description.lower()
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)


def test_caixa_invoice_pipeline_retains_debt_balances_and_credit_card_account_type():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_invoice())
    with patch.object(pdf_parser, "is_textract_enabled", return_value=False):
        a = build_default_conversion_pipeline().run(filename="fatura.pdf", raw_bytes=b"synthetic",
            analysis_id="an_caixa_invoice", pdf_parser=lambda *args, **kw: result).analysis_data
    assert a.opening_balance == -100 and a.closing_balance == -265
    assert a.ofx_account_type == "credit_card" and a.bank_code == "104"
    assert a.document_type == "credit_card_statement"
    assert len(a.report_transactions) == 5


@pytest.mark.parametrize("before,after", [
    ("200,00D", "200,01D"), ("60.00D", "6O,00D"), ("5,00C", "5,00D"),
    ("Total COMPRAS", "Total ilegível"), ("Total final (cartão 1111)", "Total ilegível"),
    ("15/10/2026", "31/02/2026"), ("16/07", "32/07"),
    ("R$ 265,00", "R$ 266,00"), ("LOJA EXEMPLO", ""),
])
def test_caixa_invoice_missing_or_inconsistent_controls_cannot_become_generic_transactions(before, after):
    source = modify(caixa_invoice(), before, after)
    if before in {"200,00D", "5,00C", "R$ 265,00"}:
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        assert len(result.transactions) == 5
        assert result.parse_metrics["financial_controls_validated"] == 0
        assert "financial_validation_failed" in result.canonical_transactions[0].warnings
    else:
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


def test_caixa_invoice_missing_geometry_is_not_silently_dropped():
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(modify(caixa_invoice(), "60.00D", missing_bbox=True))


def test_caixa_invoice_handwriting_cannot_supply_a_financial_value():
    extraction = caixa_invoice()
    page = extraction.pages[1]
    source = next(line for line in page.lines if line.text == "200,00D")
    word = ExtractedWord("handwritten", 2, 1, source.text, bbox=source.bbox, text_type="HANDWRITING")
    extraction.pages[1] = replace(page, lines=[replace(line, word_ids=(word.id,)) if line.id == source.id else line
                                              for line in page.lines], words=[word])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_caixa_invoice_dated_financial_row_without_amount_cannot_be_ignored():
    extraction = caixa_invoice()
    page = extraction.pages[1]
    extra = ocr([[('16/09', .52, .22), ('AJUSTE ESTORNO', .56, .22)] ]).pages[0].lines
    extraction.pages[1] = replace(page, lines=[*page.lines, *[replace(line, id='extra'+line.id, page_number=2) for line in extra]])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_caixa_invoice_anchors_follow_shifted_columns_and_do_not_depend_on_ocr_line_order():
    extraction = caixa_invoice()
    extraction = replace(extraction, pages=[replace(page, lines=[replace(line, bbox={**line.bbox,
        "left":line.bbox["left"]-.02, "top":line.bbox["top"]+.015}) for line in reversed(page.lines)]) for page in extraction.pages])
    parsed = pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)
    assert [row.amount for row in parsed.transactions] == [100, 5, -10, -200, -60]
