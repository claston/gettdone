import pytest

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages


def ocr(pages):
    return RawDocumentExtraction("aws_textract", "synthetic", [ExtractedPage(p, lines=[
        ExtractedLine(f"p{p}l{i}", p, i, text,
                      bbox={"left": x, "top": y, "width": .04, "height": .006})
        for i, (text, x, y) in enumerate(lines, 1)]) for p, lines in enumerate(pages, 1)], {})


def stone_old():
    header = [("Extrato de conta corrente Stone", .08, .03), ("DATA", .09, .10), ("TIPO", .17, .10),
              ("LANÇAMENTO", .25, .10), ("VALOR (R$)", .49, .10),
              ("SALDO (R$)", .66, .10), ("CONTRAPARTE", .77, .10)]
    return ocr([header + [
        ("03/01/2025 Débito", .08, .20), ("Transferência Pix", .25, .20), ("30,00", .53, .20), ("115,00", .70, .20),
        ("02/01/2025 Débito", .08, .30), ("Tarifa", .25, .30), ("-5,00", .53, .30), ("145,00", .70, .30),
    ], header + [
        ("02/01/2025 Crédito", .08, .20), ("Recebimento vendas Visa Débito", .25, .20),
        ("100,00", .53, .20), ("145,00", .70, .20),
    ]])


def bradesco():
    return ocr([[
        ("Extrato Mensal / Por Período bradesco net empresa", .1, .03), ("Folha 1/10", .84, .06),
        ("Data", .10, .10), ("Lançamento", .19, .10), ("Dcto.", .44, .10),
        ("Crédito (R$)", .54, .10), ("Débito (R$)", .67, .10), ("Saldo (R$)", .81, .10),
        ("30/04/2026", .1, .15), ("SALDO ANTERIOR", .19, .15), ("100,00", .83, .15),
        ("04/05/2026", .1, .20), ("STONE MASTER DEBITO", .19, .20), ("000001", .44, .20),
        ("50,00", .58, .20), ("150,00", .83, .20),
    ], [
        ("Extrato Mensal / Por Período bradesco net empresa", .1, .03), ("Folha 2/10", .84, .06),
        ("PIX ENVIADO", .19, .08), ("000002", .44, .08), ("20,00", .70, .08), ("130,00", .83, .08),
    ]])


def banrisul_ocr():
    return ocr([[
        ("BVP-PORTAL BANRISUL", .1, .03), ("MOVIMENTOS DA CONTA CORRENTE", .17, .08),
        ("PERIODO: 01/09/2026 até 30/09/2026", .1, .06),
        ("OPERACAO", .17, .10), ("DOCUMENTO", .46, .10), ("VALOR", .60, .10),
        ("SALDO", .70, .10), ("SIST.", .79, .10),
        ("SALDO ANT EM 31/08/2026", .17, .15), ("100,00-", .70, .15),
        ("01/09/2026 1930 VERO ANTECIPACAO", .08, .20), ("000001", .46, .20), ("150,00", .60, .20),
        ("BRR", .79, .20), ("SALDO NA DATA", .17, .25), ("50,00", .70, .25),
    ], [
        ("BVP-PORTAL BANRISUL", .1, .03), ("1005 PGTO BOLETO", .17, .08),
        ("000002", .46, .08), ("20,00-", .60, .08), ("BDX", .79, .08),
        ("SALDO NA DATA", .17, .11), ("30,00", .70, .11),
    ]])


def test_stone_credit_debit_variant_keeps_all_pages_signs_and_shared_net_fee_balance():
    result = _parse_scanned_pdf_with_textract_text_pages(stone_old())
    assert [r.amount for r in result.transactions] == [100, -5, -30]
    assert [r.date for r in result.transactions] == ["2025-01-02", "2025-01-02", "2025-01-03"]
    assert [r.source_page for r in result.canonical_transactions] == [2, 1, 1]
    assert result.parse_metrics["verified_opening_balance"] == 50
    assert result.parse_metrics["verified_closing_balance"] == 115
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_stone_numeric_ocr_error_cannot_be_replaced_with_a_balance_difference():
    ext = stone_old()
    ext.pages[0].lines[9] = ExtractedLine("wrong", 1, 10, "31,00", bbox=ext.pages[0].lines[9].bbox)
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_bradesco_uses_columns_over_description_and_preserves_dateless_page_continuation():
    result = _parse_scanned_pdf_with_textract_text_pages(bradesco())
    assert [r.amount for r in result.transactions] == [50, -20]
    assert [r.date for r in result.transactions] == ["2026-05-04"] * 2
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_bradesco_fragment_without_initial_date_is_not_silently_truncated():
    ext = bradesco()
    ext.pages.pop(0)
    with pytest.raises(PdfFinancialValidationError, match="primeira|cabeçalho|data"):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_bradesco_missing_page_cannot_receive_a_validated_closing_balance():
    ext = bradesco()
    old = ext.pages[1].lines[1]
    ext.pages[1].lines[1] = ExtractedLine(old.id, 2, old.line_index, "Folha 3/10", bbox=old.bbox)
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_bradesco_cannot_drop_two_offsetting_rows_without_document_numbers():
    ext = bradesco()
    for index, (text, x, y) in enumerate([
        ("PIX RECEBIDO", .19, .30), ("10,00", .58, .30), ("140,00", .83, .30),
        ("PIX ENVIADO", .19, .40), ("10,00", .70, .40), ("130,00", .83, .40),
    ], 10):
        ext.pages[1].lines.append(ExtractedLine(str(index), 2, index, text,
            bbox={"left":x, "top":y, "width":.04, "height":.006}))
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_banrisul_visual_reader_validates_day_and_page_continuations():
    result = _parse_scanned_pdf_with_textract_text_pages(banrisul_ocr())
    assert [r.amount for r in result.transactions] == [150, -20]
    assert [r.date for r in result.transactions] == ["2026-09-01"] * 2
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == 30
    assert result.parse_metrics["printed_balance_checks"] == 2
