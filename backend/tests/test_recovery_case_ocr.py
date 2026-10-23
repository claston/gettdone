"""Synthetic coordinates for audited OCR failure modes."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from tests.test_recovery_sweep_ocr import extraction


def bb_source():
    return extraction([
        ("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", .1, .02),
        ("Histórico", .35, .1), ("Documento", .65, .1), ("Valor R$", .75, .1), ("Saldo", .86, .1),
        ("30/06/2026", .1, .2), ("000 Saldo Anterior", .35, .2), ("100,00 C", .84, .2),
        ("01/07/2026", .1, .3), ("144 Pix Enviado", .35, .3), ("123.001", .65, .3),
        ("20,00 D", .75, .3), ("80,00 C", .84, .3),
        ("31/07/2026", .1, .4), ("999 S A L D O", .35, .4), ("80,00 C", .84, .4),
    ])


def test_bb_merged_header_and_lot_history_line_use_word_coordinates():
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text="Dt. balancete Dt. movimento Ag. origem Lote Histórico",
                     bbox={**line.bbox, "left": .1, "width": .32}) if line.text == "Histórico" else line for line in page.lines]
    lines = [replace(line, text="00000 000 Saldo Anterior", word_ids=("lot", "opening"),
                     bbox={**line.bbox, "left": .28, "width": .22}) if line.text == "000 Saldo Anterior" else line for line in lines]
    words = [
        ExtractedWord("header", 1, 1, "Histórico", bbox={"left": .35, "top": .1, "width": .07, "height": .01}),
        ExtractedWord("lot", 1, 2, "00000", bbox={"left": .28, "top": .2, "width": .04, "height": .01}),
        ExtractedWord("opening", 1, 3, "000 Saldo Anterior", bbox={"left": .35, "top": .2, "width": .15, "height": .01}),
    ]
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines, words=words)]))
    assert [t.amount for t in result.transactions] == [-20]
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("stroke", ["I", "l", "|"])
def test_bb_vertical_table_stroke_does_not_hide_explicit_debit(stroke):
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text=f"20,00 {stroke} D") if line.text == "20,00 D" else line for line in page.lines]
    lines.extend(extraction([("Limite e CET", .35, .5), ("9.999,00 C", .84, .5)]).pages[0].lines)
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [t.amount for t in result.transactions] == [-20]
    assert result.parse_metrics["verified_closing_balance"] == 80


def test_bb_merged_document_amount_and_balance_use_separate_word_columns():
    source = bb_source()
    page = source.pages[0]
    lines = [line for line in page.lines if line.text not in {"20,00 D", "80,00 C"} or line.bbox["top"] != .3]
    lines = [replace(line, text="123.001 20,00 D 80,00 C", word_ids=("doc", "value", "balance"),
                     bbox={**line.bbox, "width": .27}) if line.text == "123.001" else line for line in lines]
    words = [ExtractedWord(key, 1, i, text, bbox={"left": x, "top": .3, "width": .07, "height": .01})
             for i, (key, text, x) in enumerate([("doc", "123.001", .65), ("value", "20,00 D", .75), ("balance", "80,00 C", .84)], 1)]
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines, words=words)]))
    assert [t.amount for t in result.transactions] == [-20]


def test_bb_closing_lot_and_history_can_be_one_ocr_word_before_footer():
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text="00000 999 S ALDO", bbox={**line.bbox, "left": .28})
             if line.text == "999 S A L D O" else line for line in page.lines]
    lines.extend(extraction([("Limite e CET", .35, .5), ("9.999,00 C", .84, .5)]).pages[0].lines)
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert result.parse_metrics["verified_closing_balance"] == 80


def test_bb_blocked_balance_label_cannot_replace_cash_closing_control():
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text="999 Saldo Bloqueado") if line.text == "999 S A L D O" else line for line in page.lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


def uniprime_source():
    return extraction([
        ("Uniprime Cooperativa Extrato de 01/07/2026 a 31/07/2026", .07, .03),
        ("Saldo Anterior:", .07, .1), ("100,00", .85, .1),
        ("Data", .07, .15), ("Nº Doc", .2, .15), ("Histórico", .33, .15),
        ("Valor (R$)", .7, .15), ("Saldo (R$)", .82, .15),
        ("01/07/2026", .07, .2), ("1001", .2, .2), ("PIX TESTE", .33, .2),
        ("50,00 +", .73, .2), ("150,00", .85, .2),
        ("02/07/2026", .07, .3), ("1002", .2, .3), ("PAGAMENTO TESTE", .33, .3),
        ("20,00 -", .73, .3), ("130,00", .85, .3),
        ("Saldo em 05/08/2026", .07, .4), ("999,00", .85, .4),
        ("CET Custo Efetivo Total", .07, .5), ("10,00", .85, .5),
    ])


def test_uniprime_has_specific_layout_and_validates_cash_rows_only():
    result = pdf_parser._parse_textract_text_pages(uniprime_source())
    assert result.layout.layout_name == "uniprime_conta_corrente_ocr_v1"
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 130


@pytest.mark.parametrize("old,new", [("20,00 -", "21,00 -"), ("20,00 -", "20,00 +"), ("130,00", "131,00")])
def test_uniprime_never_changes_digits_or_explicit_sign_to_force_balance(old, new):
    source = uniprime_source()
    page = source.pages[0]
    lines = [replace(line, text=new) if line.text == old else line for line in page.lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


def test_uniprime_missing_sign_requires_exact_adjacent_printed_balances_and_review():
    source = uniprime_source()
    page = source.pages[0]
    lines = [replace(line, text="20,00") if line.text == "20,00 -" else line for line in page.lines]
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [t.amount for t in result.transactions] == [50, -20]
    assert "amount_sign_from_printed_balances" in result.canonical_transactions[-1].warnings
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_uniprime_unreadable_date_cannot_silently_drop_a_cash_row():
    source = uniprime_source()
    page = source.pages[0]
    lines = [replace(line, text="??/07/2026") if line.text == "02/07/2026" else line for line in page.lines]
    with pytest.raises(PdfFinancialValidationError, match="data legível"):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


def test_uniprime_text_without_geometry_cannot_claim_validated_balances():
    pages = ["\n".join(line.text for line in uniprime_source().pages[0].lines)]
    with pytest.raises(PdfFinancialValidationError, match="coordenadas"):
        pdf_parser._parse_pdf_transactions_from_page_texts(pages)


@pytest.mark.parametrize("mode", ["text", "tables"])
@pytest.mark.parametrize("text", [
    "Santander EXTRATO CONSOLIDADO INTELIGENTE\nPagina: 2/17",
    "Bradesco Extrato Mensal\nPágina: 3 de 8",
    "02RETORNO01COBRANCA" + "0" * 300,
])
def test_ocr_rejections_happen_before_segmentation_or_table_fallback_and_keep_evidence(mode, text, monkeypatch):
    from types import SimpleNamespace

    from app.application.errors import UnsupportedDocumentContentError

    source = extraction([(line, .1, i * .03) for i, line in enumerate(text.splitlines(), 1)])
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: SimpleNamespace(
        analyze_pdf=lambda **kwargs: {"metrics": {"textract_mode": mode}}))
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: source)
    expected = UnsupportedDocumentContentError if text.startswith("02RETORNO") else PdfFinancialValidationError
    with pytest.raises(expected) as error:
        pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert error.value._ocr_source_extraction is source
    assert error.value._parse_observability["textract_used"] == 1
    assert error.value._parse_observability["ocr_result_received"] == 1
