"""Fictional variants of dated ledgers; independent controls remain mandatory."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.parsers.pdf.layout_specific.btg_summary import BtgSummaryLayoutParser
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext
from app.application.parsers.pdf.models import _PdfLine
from tests.test_final_recovery_native import brb_page
from tests.test_recovery_case_ocr import bb_source
from tests.test_recovery_sweep_ocr import extraction


def test_brb_signed_row_can_use_only_its_separately_printed_history():
    text = brb_page().replace("27/08 COMÉRCIO TESTE — 0105 R$ +100,00", "27/08 R$ +100,00")
    result = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert [row.amount for row in result.transactions] == [-10, 100]
    assert result.transactions[1].description == "CRÉDITO PIX – DOC: 100001"


def test_bb_ocr_uses_explicit_movement_date_instead_of_backdated_booking_date():
    source = bb_source()
    page = source.pages[0]
    # The explicit movement date takes precedence for both native and OCR.
    source = replace(source, pages=[replace(page, lines=[*page.lines,
        *extraction([("02/07/2026", .22, .3)]).pages[0].lines])])
    result = pdf_parser._parse_textract_text_pages(source)
    assert result.transactions[0].date == "2026-07-02"


def test_sicoob_ascending_rows_keep_same_day_order_and_merged_date_document():
    source = extraction([
        ("SICOOB SISBR EXTRATO DE CONTA CORRENTE", .1, .02), ("Periodo: 01/12/2025 31/12/2025", .1, .06),
        ("Data", .05, .10), ("Documento", .15, .10), ("Histórico", .3, .10), ("Valor", .9, .10),
        ("30/11", .05, .20), ("SALDO ANTERIOR", .3, .20), ("R$ 100,00C", .88, .20),
        ("01/12 Pix", .05, .30), ("PIX RECEBIDO", .3, .30), ("R$ 50,00C", .88, .30),
        ("01/12", .05, .40), ("1002", .15, .40), ("PAGAMENTO", .3, .40), ("R$ 20,00D", .88, .40),
        ("01/12", .05, .50), ("SALDO DO DIA", .3, .50), ("R$ 130,00C", .88, .50),
        ("RESUMO", .3, .60), ("Saldo em conta: R$ 130,00C", .3, .65),
    ])
    # Textract WORD boxes split a merged date/document LINE without rewriting tokens.
    from app.application.document_extraction_models import ExtractedWord

    page = source.pages[0]
    merged = next(line for line in page.lines if line.text == "01/12 Pix")
    words = [ExtractedWord("date", 1, 1, "01/12", bbox={**merged.bbox, "width": .06}),
             ExtractedWord("doc", 1, 2, "Pix", bbox={**merged.bbox, "left": .15, "width": .05})]
    source = replace(source, pages=[replace(page, words=words, lines=[replace(line, word_ids=("date", "doc"))
        if line is merged else line for line in page.lines])])
    result = pdf_parser._parse_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [50, -20]
    assert [row.external_reference_id for row in result.canonical_transactions] == ["Pix", "1002"]
    assert result.parse_metrics["verified_closing_balance"] == 130


@pytest.mark.parametrize("ascending", [False, True])
def test_btg_summary_accepts_both_printed_directions_without_losing_controls(ascending):
    header = ["Resumo do saldo", "Saldo de abertura 01/08/2026 R$ 100,00", "Saldo de fechamento 31/08/2026 R$ 130,00",
        "Total de entradas R$ 50,00", "Total de saídas R$ 20,00", "Período de 01/08/2026 a 31/08/2026",
        "Data de lançamento Descrição do lançamento Entradas/Saídas Saldo"]
    ledger = ["01/08/2026 Saldo de abertura R$ 100,00", "02/08/2026 Pix recebido R$ 50,00 R$ 150,00",
              "03/08/2026 Pix enviado - R$ 20,00 R$ 130,00", "31/08/2026 Saldo de fechamento R$ 130,00"]
    footer = ["Extrato conta corrente", "Período de 01/08/2026 a 31/08/2026 - EMPRESA FICTÍCIA",
        "Banco 208 · Agência 1 · Conta 12345", "© 2026 BTG Pactual. 1 / 1"]
    texts = [*header, *(ledger if ascending else reversed(ledger)), *footer]
    lines = [_PdfLine(text, 1, index) for index, text in enumerate(texts, 1)]
    result = BtgSummaryLayoutParser().parse(layout_name="btg_pactual_extrato_conta_corrente_resumo_v1",
        lines=lines, context=LayoutSpecificParseContext())
    assert [row.transaction.amount for row in result.rows] == [50, -20]
