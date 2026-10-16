from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord
from app.application.document_type_classifier import classify_document_type
from app.application.errors import UnsupportedDocumentContentError
from app.application.parsers.pdf.layout_specific.itau_monthly_ocr import parse_itau_monthly_ocr
from app.application.pdf_document_segmentation import segment_pdf_pages
from app.application.pdf_parser import parse_pdf_transactions

STATEMENT = ("Itaú\nExtrato mensal\nConta corrente\nAg 1234 CC 12345-6 jun 2026\nData Descrição Entradas Saídas Saldo\n"
             "05/06 PIX RECEBIDO 350,00\nSaldo anterior 1,00")
INVOICE = ("PREFEITURA MUNICIPAL\nNOTA FISCAL DE SERVIÇOS ELETRÔNICA NFS-e\nPRESTADOR DE SERVIÇOS\n"
           "TOMADOR DE SERVIÇOS\nVALOR DOS SERVIÇOS 350,00")
RECEIPT = "COMPROVANTE DE TRANSFERÊNCIA PIX\nVALOR 350,00\nAUTENTICAÇÃO ABC\nDATA DA TRANSAÇÃO 05/06/2026"


def test_nfse_is_identified_without_nfe_access_key():
    result = classify_document_type("invoice.pdf", b"", extracted_text=INVOICE)
    assert result.document_type == "fiscal_invoice"
    assert result.confidence >= 0.9


def test_invoice_mention_in_statement_is_not_an_invoice():
    result = classify_document_type("statement.pdf", b"", extracted_text=STATEMENT + "\nPagamento nota fiscal de serviços")
    assert result.document_type == "bank_statement"


@pytest.mark.parametrize("pages", [[STATEMENT, INVOICE, RECEIPT], [INVOICE, STATEMENT, RECEIPT]])
def test_segmentation_excludes_annexes_preserving_original_page_numbers(pages):
    result = segment_pdf_pages(pages)
    statement_page = pages.index(STATEMENT)
    assert result.page_texts[statement_page] == STATEMENT
    assert all(not text for index, text in enumerate(result.page_texts) if index != statement_page)
    assert result.source_page_texts == tuple(pages)
    assert [part["document_type"] for part in result.parts] == [
        "bank_statement" if page == STATEMENT else "fiscal_invoice" if page == INVOICE else "bank_receipt" for page in pages
    ]
    assert result.parts[statement_page]["pages"] == [statement_page + 1]
    assert all(not part["included_in_statement"] for part in result.parts if part["document_type"] != "bank_statement")


def test_mixed_pdf_keeps_evidenced_continuation_but_marks_unknown_attachment():
    continuation = "06/06 PIX ENVIADO 25,00-\nSALDO DO DIA 325,00"
    result = segment_pdf_pages([STATEMENT, continuation, INVOICE, "DOCUMENTO NÃO IDENTIFICADO"])
    assert result.page_texts == (STATEMENT, continuation, "", "")
    assert result.parts[-1]["requires_review"] is True


def test_different_bank_accounts_are_not_silently_merged():
    other = STATEMENT.replace("12345-6", "98765-4")
    with pytest.raises(UnsupportedDocumentContentError, match="contas"):
        segment_pdf_pages([STATEMENT, INVOICE, other])


def test_single_statement_remains_unchanged():
    assert segment_pdf_pages([STATEMENT]).page_texts == (STATEMENT,)


def test_amount_after_current_account_label_is_not_mistaken_for_an_account_number():
    first = "EXTRATO MENSAL\nConta corrente 1234,56\n01/06 PIX 10,00\nSALDO 1234,56"
    second = first.replace("1234,56", "1244,56")
    result = segment_pdf_pages([first, second, INVOICE])
    assert result.page_texts == (first, second, "")


def _line(identifier, text, left, top, width=0.05):
    return ExtractedLine(identifier, 1, int(top * 10000), text, bbox={"left": left, "top": top, "width": width, "height": 0.008})


def _word(identifier, text, left, top, text_type="PRINTED"):
    return ExtractedWord(identifier, 1, 1, text, bbox={"left": left, "top": top, "width": 0.05, "height": 0.008}, text_type=text_type)


def _monthly_page():
    return ExtractedPage(1, lines=[
        _line("title", "Itaú Extrato mensal Conta corrente jun 2026", 0.1, 0.05),
        _line("date-header", "data", 0.25, 0.1),
        _line("description-header", "descrição", 0.35, 0.1),
        _line("credit-header", "entradas R$", 0.59, 0.1),
        _line("debit-header", "saídas R$", 0.71, 0.1),
        _line("balance-header", "saldo R$", 0.86, 0.1),
        _line("date", "05/06", 0.25, 0.2),
        _line("desc-credit", "PIX RECEBIDO CLIENTE", 0.35, 0.2),
        _line("credit-merged", "350,00 - NOTA12 20,00-", 0.61, 0.198, 0.18),
        _line("desc-debit", "PIX ENVIADO FORNECEDOR", 0.35, 0.215),
        _line("desc-app", "SALDO APLIC AUT MAIS", 0.35, 0.235),
    ], words=[
        _word("credit", "350,00", 0.61, 0.2),
        _word("dash", "-", 0.67, 0.2, "HANDWRITING"),
        _word("note", "NOTA12", 0.77, 0.2),
        _word("debit", "20,00-", 0.71, 0.215),
        _word("balance", "331,00", 0.86, 0.215),
        _word("investment", "9.000,00", 0.86, 0.235),
    ])


def test_monthly_ocr_uses_columns_and_word_positions_without_filtering_all_handwriting():
    result = parse_itau_monthly_ocr([_monthly_page()])
    assert [row.transaction.amount for row in result.rows] == [350.0, -20.0]
    assert [row.transaction.date for row in result.rows] == ["2026-06-05", "2026-06-05"]
    assert result.rows[1].running_balance == 331.0
    assert result.rows[0].source_page == 1
    assert result.unresolved_rows == 0
    assert result.annotations_detected > 0


def test_monthly_ocr_reports_unresolved_row_instead_of_inventing_a_value():
    page = _monthly_page()
    page.words[:] = [word for word in page.words if word.id != "debit"]
    result = parse_itau_monthly_ocr([page])
    assert [row.transaction.amount for row in result.rows] == [350.0]
    assert result.unresolved_rows == 1


def test_monthly_ocr_refuses_missing_year_and_unpositioned_values():
    page = _monthly_page()
    page.lines[:] = [line for line in page.lines if line.id != "title"]
    result = parse_itau_monthly_ocr([page])
    assert result.rows == []
    assert result.unresolved_rows >= 2


def test_monthly_ocr_keeps_date_across_pages_and_preserves_real_duplicate_amounts():
    first = _monthly_page()
    second = replace(_monthly_page(), page_number=2)
    second.lines[:] = [replace(line, page_number=2) for line in second.lines if line.id != "date"]
    second.words[:] = [replace(word, page_number=2) for word in second.words]
    result = parse_itau_monthly_ocr([first, second])
    assert [row.transaction.amount for row in result.rows] == [350.0, -20.0, 350.0, -20.0]
    assert all(row.transaction.date == "2026-06-05" for row in result.rows)
    assert [row.source_page for row in result.rows] == [1, 1, 2, 2]


def test_monthly_ocr_does_not_import_investment_balance_or_summary_as_transactions():
    page = _monthly_page()
    page.lines.extend([
        _line("end", "Saldo em C/C", 0.35, 0.25),
        _line("investment-row", "Aplicações realizadas", 0.35, 0.3),
    ])
    page.words.append(_word("investment-credit", "9.000,00", 0.61, 0.3))
    result = parse_itau_monthly_ocr([page])
    assert [row.transaction.amount for row in result.rows] == [350.0, -20.0]


def test_monthly_ocr_flags_handwritten_financial_value_for_review():
    page = _monthly_page()
    page.words[:] = [replace(word, text_type="HANDWRITING") if word.id == "credit" else word for word in page.words]
    result = parse_itau_monthly_ocr([page])
    assert [row.transaction.amount for row in result.rows] == [-20.0]
    assert result.unresolved_rows == 1


def test_monthly_ocr_keeps_movement_header_when_investment_table_repeats_date_label():
    page = _monthly_page()
    page.lines.extend([
        _line("end", "Saldo em C/C", 0.35, 0.25),
        _line("investment-date-header", "data", 0.1, 0.5),
    ])
    result = parse_itau_monthly_ocr([page])
    assert [row.transaction.amount for row in result.rows] == [350.0, -20.0]
    assert result.unresolved_rows == 0


def test_monthly_ocr_reports_amount_without_a_description_instead_of_silently_losing_it():
    page = _monthly_page()
    page.words.append(_word("orphan-value", "75,00", 0.61, 0.26))
    page.words.append(ExtractedWord("unpositioned-note", 1, 8, "note"))
    result = parse_itau_monthly_ocr([page])
    assert [row.transaction.amount for row in result.rows] == [350.0, -20.0]
    assert result.unresolved_rows == 1


def native_pdf(pages):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Courier"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        commands = ["BT", "/F1 10 Tf", "40 800 Td"]
        for index, line in enumerate(text.splitlines()):
            if index:
                commands.append("0 -14 Td")
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_native_pdf_with_invoice_first_uses_only_statement_and_original_source_page(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    statement = "EXTRATO DE CONTA CORRENTE\n01/06/2026 PIX RECEBIDO 350,00\n02/06/2026 PIX ENVIADO -20,00\nSALDO FINAL 330,00"
    raw = native_pdf([INVOICE, statement, RECEIPT])
    parsed = parse_pdf_transactions(raw)
    assert [row.amount for row in parsed.transactions] == [350.0, -20.0]
    assert all(row.source_page == 2 for row in parsed.canonical_transactions)
    result = build_default_conversion_pipeline().run(filename="compound.pdf", raw_bytes=raw, analysis_id="an_compound_native")
    analysis = result.analysis_data
    assert analysis.transactions_total == 2
    assert analysis.net_total == 330.0
    assert [part["document_type"] for part in analysis.document_parts] == ["fiscal_invoice", "bank_statement", "bank_receipt"]
    assert "PRESTADOR" not in result.parsed_document.extracted_text


def test_standalone_nfse_is_rejected_without_transaction_or_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with pytest.raises(UnsupportedDocumentContentError):
        build_default_conversion_pipeline().run(filename="invoice.pdf", raw_bytes=native_pdf([INVOICE]), analysis_id="an_invoice")
