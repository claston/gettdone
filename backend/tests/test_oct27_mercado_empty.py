"""A fictional native Mercado Pago statement whose printed ledger is empty."""
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.followup_native_tables import parse_followup_native_tables
from app.application.parsers.pdf.native_table_source import read_native_table_source
from tests.fixtures.recovery_acceptance import grid_pdf


def mercado_empty(*, mutation=None, balance="150,00", scale=1):
    cells = [("EXTRATO DE CONTA", .73, .05), ("EMPRESA FICTÍCIA", .65, .07),
        ("CPF/CNPJ: 00000000000000 Agência: 1 Conta: 123456789", .48, .09),
        ("Periodo: De 25-10-2026 al 25-10-2026", .65, .11),
        (f"Saldo inicial: R$ {balance}", .10, .17), ("Entradas: R$ 0,00", .40, .16),
        ("Saidas: R$ 0,00", .40, .19), (f"Saldo final: R$ {balance}", .72, .17),
        ("DETALHE DOS MOVIMENTOS", .39, .25),
        ("Data", .09, .29), ("Descrição", .20, .29), ("ID da operação", .44, .29),
        ("Valor", .70, .29), ("Saldo", .86, .29),
        ("Data de geração: 26-10-2026", .07, .88),
        ("Mercado Pago Instituição de Pagamento Ltda.", .07, .95), ("1/1", .89, .97)]
    substitutions = {
        "different_balance": {f"Saldo final: R$ {balance}": "Saldo final: R$ 151,00"},
        "nonzero_entries": {"Entradas: R$ 0,00": "Entradas: R$ 1,00"},
        "wrong_period": {"Periodo: De 25-10-2026 al 25-10-2026": "Periodo: De 26-10-2026 al 25-10-2026"},
        "invalid_date": {"Periodo: De 25-10-2026 al 25-10-2026": "Periodo: De 32-10-2026 al 32-10-2026"},
        "incomplete": {"1/1": "1/2"},
        "invalid_generation": {"Data de geração: 26-10-2026": "Data de geração: 32-10-2026"},
        "old_generation": {"Data de geração: 26-10-2026": "Data de geração: 24-10-2026"},
    }
    omitted = {"missing_column": "Descrição", "missing_footer": "Data de geração: 26-10-2026",
               "missing_account": "CPF/CNPJ: 00000000000000 Agência: 1 Conta: 123456789"}
    cells = [(substitutions.get(mutation, {}).get(text, text), x, y)
             for text, x, y in cells if text != omitted.get(mutation)]
    if mutation in ("unreadable_body", "extra_amount", "orphan_date"):
        text = {"unreadable_body": "MOVIMENTO ILEGÍVEL", "extra_amount": "R$ 1,00", "orphan_date": "25-10-2026"}[mutation]
        cells.append((text, .20, .40))
    if mutation == "extra_summary":
        cells.append(("Entradas: R$ 0,00", .40, .21))
    if mutation == "misaligned_column":
        cells = [(text, x, .35 if text == "Descrição" else y) for text, x, y in cells]
    if mutation == "active_zero_totals":
        cells += [("25-10-2026", .09, .40), ("Pix recebido FICTÍCIO", .20, .40), ("123456", .44, .40),
                  ("R$ 5,00", .70, .40), ("R$ 155,00", .86, .40)]
    return grid_pdf([cells, cells] if mutation == "multiple_pages" else [cells], scale)


@pytest.mark.parametrize("balance,scale", [("150,00", 1), ("0,00", .7), ("-150,00", 1.5)])
def test_native_empty_mercado_keeps_the_printed_balance_without_inventing_transactions(monkeypatch, balance, scale):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(mercado_empty(balance=balance, scale=scale))
    expected = float(balance.replace(",", "."))
    assert result.transactions == []
    assert result.canonical_transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["selected_parser"] == "layout_confirmed_no_activity"
    assert result.parse_metrics["verified_opening_balance"] == expected
    assert result.parse_metrics["verified_closing_balance"] == expected
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["textract_attempted"] == 0
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf",
        raw_bytes=mercado_empty(balance=balance, scale=scale), analysis_id="an_mercado_empty_fictional").analysis_data
    assert analysis.transactions_total == 0
    assert (analysis.opening_balance, analysis.closing_balance) == (expected, expected)
    assert analysis.bank_code == "323"


@pytest.mark.parametrize("mutation", ["different_balance", "nonzero_entries", "wrong_period", "invalid_date",
    "incomplete", "missing_column", "missing_footer", "missing_account", "unreadable_body", "extra_amount",
    "orphan_date", "extra_summary", "multiple_pages", "misaligned_column", "invalid_generation", "old_generation"])
def test_unproven_or_incomplete_mercado_is_never_accepted_as_empty(monkeypatch, mutation):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(mercado_empty(mutation=mutation))


def _with_image(raw, *, body):
    writer = PdfWriter(clone_from=BytesIO(raw))
    page = writer.pages[0]
    image = DecodedStreamObject()
    image.set_data(Image.new("RGB", (20, 20), "black").tobytes())
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(20), NameObject("/Height"): NumberObject(20),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
    page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Test"): writer._add_object(image)})
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + (b"\nq 100 0 0 50 200 400 cm /Test Do Q" if body
                                                   else b"\nq 100 0 0 50 50 720 cm /Test Do Q"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    assert len(PdfReader(BytesIO(result.getvalue())).pages[0].images) == 1
    return result.getvalue()


def test_image_in_the_ledger_cannot_be_mistaken_for_an_empty_table():
    raw = _with_image(mercado_empty(), body=True)
    assert parse_followup_native_tables(raw, pdf_parser._read_native_pdf_page_texts(raw)) is None


def test_logo_above_the_ledger_does_not_hide_empty_statement_controls():
    result = pdf_parser.parse_pdf_transactions(_with_image(mercado_empty(), body=False))
    assert result.parse_metrics["statement_no_activity"] == 1


def test_ocr_text_cannot_prove_that_the_native_table_is_empty():
    from app.application.parsers.pdf.mercado_pago_inactive import parse_mercado_pago_inactive

    raw = mercado_empty()
    source = replace(read_native_table_source(raw), provider="textract")
    assert parse_mercado_pago_inactive(raw, source) is None


def test_zero_summary_does_not_hide_printed_movements_or_real_financial_differences():
    result = pdf_parser.parse_pdf_transactions(mercado_empty(mutation="active_zero_totals"))
    assert [row.amount for row in result.transactions] == [5]
    assert not result.parse_metrics.get("statement_no_activity")
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings
