from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import parse_pdf_transactions


def monthly_pdf(*, wrong_balance=False, wrong_total=False, missing_row=False, shuffled=False,
                cash_only=False, appendix=False, missing_auto_totals=False):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    page = writer.add_blank_page(width=600, height=800)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    cells = []

    def cell(x, y, text):
        cells.append((x, y, text))

    cell(25, 775, "extrato mensal Itau ag 1234 cc 56789-0 set 2026 001|001")
    cell(25, 750, "01. Conta Corrente e Aplicações Automáticas")
    cell(25, 725, "saldo em 31/08/26 R$ 1.000,00 saldo em 30/09/26 R$ 1.150,00")
    cell(25, 705, "entradas (créditos) total R$ " + ("201,00" if wrong_total else "200,00"))
    cell(25, 685, "saídas (débitos) total R$ 50,00")
    for x, text in [(30, "data"), (95, "descrição"), (320, "entradas R$"), (410, "saídas R$"), (515, "saldo R$")]:
        cell(x, 650, text)
    cell(30, 630, "31/08")
    cell(95, 630, "Saldo anterior")
    cell(510, 630, "1.000,00")
    cell(30, 610, "01/09")
    cell(95, 610, "PIX RECEBIDO CLIENTE")
    cell(330, 610, "200,00")
    if cash_only:
        cell(510, 610, "1.201,00" if wrong_balance else "1.200,00")
    if not cash_only and not missing_row:
        cell(95, 590, "Apl Aplic Aut Mais")
        cell(420, 590, "200,00-")
        cell(530, 590, "11,00" if wrong_balance else "10,00")
    if not cash_only:
        cell(95, 570, "SALDO APLIC AUT MAIS")
        cell(510, 570, "1.190,00")
    cell(30, 550, "02/09")
    cell(95, 550, "PAGAMENTO FORNECEDOR")
    cell(420, 550, "50,00-")
    if cash_only:
        cell(510, 550, "1.150,00")
    else:
        cell(95, 530, "Res Aplic Aut Mais")
        cell(330, 530, "50,00")
        cell(530, 530, "10,00")
        cell(95, 510, "SALDO APLIC AUT MAIS")
        cell(510, 510, "1.140,00")
    cell(95, 490, "Saldo em C/C")
    cell(510, 490, "1.150,00" if cash_only else "10,00")
    cell(95, 470, "Saldo final")
    cell(510, 470, "1.150,00")
    if not cash_only and not missing_auto_totals:
        cell(25, 445, "totalizador de aplicações automáticas entrada R$ saída R$")
        cell(25, 425, "na conta corrente (1) 50,00 200,00-")
    if appendix:
        cell(25, 400, "Conta Corrente | Débitos automáticos efetuados")
        cell(95, 375, "data histórico valor R$ data histórico valor R$")
        cell(95, 350, "03/09/26 DEBITO SEGURO 01/60")
        cell(420, 350, "50,00")
        cell(510, 350, "25,00")
        cell(25, 320, "02. Investimentos | CDB Renda Fixa")
        cell(510, 300, "10.000,00")
    if shuffled:
        cells = cells[:5] + sorted(cells[5:], key=lambda item: (-item[0], -item[1]))
    stream = DecodedStreamObject()
    stream.set_data("\n".join(f"BT /F1 8 Tf 1 0 0 1 {x} {y} Tm <{text.encode('cp1252').hex()}> Tj ET"
                              for x, y, text in cells).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    target = BytesIO()
    writer.write(target)
    return target.getvalue()


@pytest.mark.parametrize("shuffled", [False, True])
def test_native_monthly_columns_keep_all_movements_and_separate_investment_positions(shuffled, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    a = build_default_conversion_pipeline().run(filename="itau.pdf", raw_bytes=monthly_pdf(shuffled=shuffled),
                                               analysis_id="an_itau_native").analysis_data
    assert [row.amount for row in a.report_transactions] == [200, -200, -50, 50]
    assert a.opening_balance == a.closing_balance == 10
    assert a.pdf_processing_metrics["selected_parser"] == "layout_specific_itau_monthly_native"
    assert a.pdf_processing_metrics["canonical_warning_count"] == 0
    assert all(row.source_regions for row in a.report_transactions)


@pytest.mark.parametrize("defect", ["wrong_balance", "wrong_total", "missing_row"])
def test_native_monthly_controls_refuse_partial_or_inconsistent_cash_ledgers(defect, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(PdfFinancialValidationError):
        parse_pdf_transactions(monthly_pdf(**{defect: True}))


@pytest.mark.parametrize("cash_only", [False, True])
def test_native_monthly_stops_at_final_balance_before_same_page_appendices(cash_only, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = parse_pdf_transactions(monthly_pdf(cash_only=cash_only, appendix=True, shuffled=True))
    assert [row.amount for row in result.transactions] == ([200, -50] if cash_only else [200, -200, -50, 50])
    assert all(row.source_regions for row in result.canonical_transactions)


def test_native_monthly_cash_only_accepts_absent_automatic_totalizer(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    a = build_default_conversion_pipeline().run(filename="cash.pdf", raw_bytes=monthly_pdf(cash_only=True),
                                               analysis_id="an_itau_cash_only").analysis_data
    assert [row.amount for row in a.report_transactions] == [200, -50]
    assert a.opening_balance == 1000 and a.closing_balance == 1150


@pytest.mark.parametrize("defect", ["wrong_balance", "wrong_total", "missing_auto_totals"])
def test_native_monthly_keeps_controls_for_cash_only_or_missing_automatic_totals(defect, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(PdfFinancialValidationError):
        parse_pdf_transactions(monthly_pdf(cash_only=defect != "missing_auto_totals", **{defect: True}))
