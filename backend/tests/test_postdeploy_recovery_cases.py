"""Fictional regressions from the post-deploy audit; no private documents."""

from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.document_type_classifier import classify_document_type
from app.application.errors import UnsupportedDocumentContentError
from app.application.pdf_document_segmentation import _statement_identity, segment_pdf_pages
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_santander_ocr_controls import santander_extraction


@pytest.fixture(autouse=True)
def no_remote_extraction(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def test_statement_identity_resolves_agency_and_account_in_label_value_rows():
    columns = "Resumo julho/2026\nAgência Conta Corrente\n1234 12.345678-9\nData Descrição Valor Saldo"
    stacked = "Extrato de Conta Corrente\nAgência 1234\nConta Corrente 12.345678-9\nData Descrição Valor Saldo"
    assert _statement_identity(columns) == _statement_identity(stacked)
    pages = [columns + "\n01/07/2026 PIX RECEBIDO 10,00 C 110,00 C", stacked]
    assert segment_pdf_pages(pages).source_page_texts == tuple(pages)


def test_santander_page_footer_is_not_an_unassigned_money_cell():
    source = santander_extraction()
    page = source.pages[1]
    footer = replace(page.lines[-1], id="footer", line_index=900, text="Pagina 2/12",
                     bbox={"left": .9, "top": .935, "width": .08, "height": .008})
    source = replace(source, pages=[source.pages[0], replace(page, lines=[*page.lines, footer]), *source.pages[2:]])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-100, -.16, 100.16, 1234.56, -1234.56, 300, -300]
    assert result.parse_metrics["partial_statement"] == 1


def mercado_related_events(retention=False):
    movements = ("02-07-2026 Pix recebido TESTE 1001 R$ 50,00 R$ 50,00\n"
                 "03-07-2026 Dinheiro retido 1001 R$ -50,00 R$ 0,00\n"
                 "04-07-2026 Liberação de dinheiro 1001 R$ 50,00 R$ 50,00") if retention else (
                     "02-07-2026 Liberação de dinheiro 1001 R$ 50,00 R$ 50,00\n"
                     "31-07-2026 Liberação de dinheiro 1001 R$ 50,00 R$ 100,00")
    return ("EXTRATO DE CONTA\nPeriodo: De 01-07-2026 al 31-07-2026\n"
            f"Saldo inicial: R$ 0,00 Entradas: R$ 100,00\nSaidas: R$ {'-50,00' if retention else '0,00'} "
            f"Saldo final: R$ {'50,00' if retention else '100,00'}\n"
            "Data Descrição ID da operação Valor Saldo\n" + movements +
            "\nMercado Pago Instituição de Pagamento Ltda.\n1/1")


@pytest.mark.parametrize("retention", [False, True])
def test_mercado_pago_preserves_related_events_with_shared_operation_reference(retention):
    raw = generate_native_text_pdf((mercado_related_events(retention).splitlines(),))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [r.amount for r in result.transactions] == ([50, -50, 50] if retention else [50, 50])
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "shared_operation_reference" in result.parse_metrics["canonical_warning_types_list"]


def test_open_payables_report_is_classified_before_bank_transaction_parsing():
    text = ("Notas a Pagar\nSituação: A Pagar\nTipo de Data: Vencimento\n"
            "Dt. Mov. Dt. Pag. Dt. Venc. Número Descrição Fornecedor Título(R$) Saldo a Pagar(R$)\n"
            "01/07/2026 10/07/2026 1001 FORNECEDOR TESTE 50,00 50,00\nSubtotal: 50,00 50,00")
    assert classify_document_type("report.pdf", b"", extracted_text=text).document_type == "accounts_payable_report"
    raw = generate_native_text_pdf((text.splitlines(),))
    with pytest.raises(UnsupportedDocumentContentError) as error:
        pdf_parser.parse_pdf_transactions(raw)
    assert error.value.document_type == "accounts_payable_report"
    from app.application.unsupported_document_detection import is_open_payables_report

    assert not is_open_payables_report("Extrato de Conta Corrente\n" + text)


def cresol_month(month, end, opening, amount, closing):
    return ("Agência 1234 Conta 12345-6\nSaldo em Conta Limite de Crédito Saldo Disponível\n"
            f"{closing} R$ 10,00\nLançamentos\n"
            f"02/{month}/2026 Saldo do Dia: {closing}\n02/{month}/2026 PIX TESTE {amount}\n"
            f"Saldo Anterior: {opening}\nConsulta Posição Consolidada em 01/10/2026\n"
            f"Período de 01/{month}/2026 a {end}/{month}/2026\nPágina 1 de 1")


def test_cresol_monthly_blocks_keep_their_own_period_and_balance_controls():
    pages = [cresol_month("06", "30", "+ R$ 100,00", "+ R$ 50,00", "+ R$ 150,00"),
             cresol_month("07", "31", "+ R$ 150,00", "- R$ 20,00", "+ R$ 130,00")]
    raw = generate_native_text_pdf(tuple(page.splitlines() for page in pages))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [r.amount for r in result.transactions] == [50, -20]
    assert [r.date for r in result.transactions] == ["2026-06-02", "2026-07-02"]
    assert [r.source_page for r in result.canonical_transactions] == [1, 2]


def test_cresol_separate_accounts_are_still_rejected():
    pages = [cresol_month("06", "30", "+ R$ 100,00", "+ R$ 50,00", "+ R$ 150,00"),
             cresol_month("07", "31", "+ R$ 150,00", "- R$ 20,00", "+ R$ 130,00").replace("12345-6", "98765-4")]
    raw = generate_native_text_pdf(tuple(page.splitlines() for page in pages))
    from app.application.errors import InvalidFileContentError

    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(raw)


@pytest.mark.parametrize("scale", [.75, 1, 2])
def test_bb_native_watermark_and_opening_on_period_start(scale):
    lines = ("Banco do Brasil", "Extrato de Conta Corrente", "Cliente: TESTE",
             "Agência 1234 Conta 12345-6", "Período: 01 a 31/07/2026", "Dia Lote Documento Histórico Valor",
             "01/07/2026 Saldo Anterior 100,00 (+)",
             "02/07/2026 99015 10001 Pix Recebido TESTE 50,00 (+)",
             "02/07/2026 Saldo do dia 150,00 (+)", "31/07/2026 Saldo 150,00 (+)")
    reader = PdfReader(BytesIO(generate_native_text_pdf((lines,))))
    writer = PdfWriter()
    page = reader.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf .707 .707 -.707 .707 100 350 Tm (OPERADOR TESTE 9.999,00) Tj ET")
    page[NameObject("/Contents")] = ArrayObject([page[NameObject("/Contents")], writer._add_object(stream)])
    page.scale_by(scale)
    writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    result = pdf_parser.parse_pdf_transactions(output.getvalue())
    assert [row.amount for row in result.transactions] == [50]
    assert "OPERADOR TESTE" not in result.extracted_text
    assert result.parse_metrics["extraction_provider"] == "native_pdfium_horizontal_text"


def test_bradesco_name_in_a_tax_payment_is_not_a_header():
    from app.application.normalization.pdf_text_rules import should_skip_transaction_description

    assert not should_skip_transaction_description("BRADESCO NET EMPRESA 10001 NET EMPRESA DARF 1708")
    from tests.test_oct20_followup_ocr import ocr

    source = ocr([[("Bradesco Extrato (Últimos Lançamentos)", .1, .02),
        ("Data", .1, .1), ("Lançamento", .2, .1), ("Dcto.", .45, .1),
        ("Crédito (R$)", .58, .1), ("Débito (R$)", .7, .1), ("Saldo (R$)", .85, .1),
        ("01/07/2026", .1, .2), ("SALDO ANTERIOR", .2, .2), ("100,00", .85, .2),
        ("02/07/2026", .1, .3), ("BRADESCO NET EMPRESA", .2, .3),
        ("10001", .45, .3), ("-10,00", .7, .3), ("90,00", .85, .3),
        ("NET EMPRESA DARF 1708", .2, .31)]])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert any(row.amount == -10 and "BRADESCO NET EMPRESA" in row.description for row in result.transactions)

