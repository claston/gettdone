from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedCell, ExtractedLine, ExtractedPage, ExtractedTable, RawDocumentExtraction
from app.application.errors import InvalidFileContentError
from app.application.normalization.ocr_numeric_separators import normalize_ocr_extraction, normalize_ocr_numeric_separators
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages
from tests.test_inter_ocr_ledger import change, inter_extraction
from tests.test_itau_monthly_ocr_separators import _page
from tests.test_nubank_ocr_daily_blocks import change_text, nubank_extraction


@pytest.mark.parametrize("raw, expected", [
    ("10,000,00", "10.000,00"), ("20,000,00-", "20.000,00-"), ("-1,234,567,89", "-1.234.567,89"),
    ("R$ 1,234,56 C", "R$ 1.234,56 C"), ("10,000,00D", "10.000,00D"),
    ("PIX 10,000,00- SALDO 2,000,00", "PIX 10.000,00- SALDO 2.000,00"),
])
def test_normalizes_exact_thousands_groups_preserving_digits_and_signs(raw, expected):
    assert normalize_ocr_numeric_separators(raw) == expected


@pytest.mark.parametrize("raw", ["20,00,00-", "20,000,0-", "20,000.00-", "-20,000,00-", "20,000,00--",
                                "NOTA20,000,00", "UUID-20,000,00", "20,000,00/9", "20.000,00", "1.234,56", "20,00"])
def test_preserves_malformed_tokens_references_and_already_valid_values(raw):
    assert normalize_ocr_numeric_separators(raw) == raw


def test_normalization_keeps_original_evidence_and_geometries_including_table_cells():
    original = _page()
    word = next(word for word in original.words if word.id == "amount-1")
    table = ExtractedTable("table", 1, 1, [[ExtractedCell(1, 1, word.text, word_ids=(word.id,), bbox=word.bbox)]])
    source = RawDocumentExtraction("aws_textract", "synthetic", [replace(original, tables=[table])], {})
    normalized = normalize_ocr_extraction(source)
    assert next(item for item in normalized.pages[0].words if item.id == word.id) == replace(word, text="20.000,00-")
    assert normalized.pages[0].tables[0].rows[0][0].text == "20.000,00-"
    assert source.pages[0].tables[0].rows[0][0].text == "20,000,00-"


def test_currency_prefix_in_a_printed_word_is_also_normalized_in_its_parent_line():
    page = _page()
    word = replace(next(word for word in page.words if word.id == "amount-1"), text="R$ 20,000,00-")
    line = ExtractedLine("line", 1, 1, word.text, word_ids=(word.id,))
    source = RawDocumentExtraction("aws_textract", "synthetic", [ExtractedPage(1, lines=[line], words=[word])], {})
    normalized = normalize_ocr_extraction(source)
    assert normalized.pages[0].lines[0].text == "R$ 20.000,00-"
    assert normalized.pages[0].words[0].text == "R$ 20.000,00-"


def test_handwritten_token_is_preserved_in_words_lines_and_cells():
    original = _page()
    original.words[:] = [replace(word, text_type="HANDWRITING") if word.id == "amount-1" else word for word in original.words]
    word = next(word for word in original.words if word.id == "amount-1")
    table = ExtractedTable("table", 1, 1, [[ExtractedCell(1, 1, word.text, word_ids=(word.id,), bbox=word.bbox)]])
    source = RawDocumentExtraction("aws_textract", "synthetic", [replace(original, tables=[table])], {})
    assert normalize_ocr_extraction(source) == source


def test_line_only_ocr_and_tables_without_words_use_the_same_rule():
    page = _page()
    source = RawDocumentExtraction("aws_textract", "synthetic", [replace(page, words=[], tables=[
        ExtractedTable("table", 1, 1, [[ExtractedCell(1, 1, "10,000,00")]])])], {})
    normalized = normalize_ocr_extraction(source)
    assert next(line.text for line in normalized.pages[0].lines if line.id == "amount-line-1") == "20.000,00-"
    assert normalized.pages[0].tables[0].rows[0][0].text == "10.000,00"


def test_shared_recovery_handles_nubank_and_preserves_the_original_ocr():
    source = change_text(nubank_extraction(), "1.100,00", "1,100,00")
    result = _parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [1234.74, 1222.36, 1100, -400, -335.75]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.source_extraction == source
    assert "1,100,00" in result.extracted_text


def test_shared_recovery_handles_an_inter_balance():
    source = change(inter_extraction(), "R$ 1.478,85", "R$ 1,478,85")
    result = _parse_scanned_pdf_with_textract_text_pages(source)
    assert result.canonical_transactions[2].running_balance == 1478.85
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.source_extraction == source


def test_other_banks_still_reject_digits_that_do_not_reconcile_after_separator_repair():
    source = change_text(nubank_extraction(), "1.100,00", "1,100,01")
    with pytest.raises(InvalidFileContentError):
        _parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.parametrize("correct", [True, False])
def test_local_ocr_uses_common_rule_and_statement_totals_without_repeating_ocr(monkeypatch, correct):
    pages = ["\n".join([
        "FINANCEHUB", "Total entradas: R$ 10.000,00", "Total saídas: R$ 0,00", "Resultado: R$ 10.000,00",
        "Saldo inicial do período: R$ 1.000,00", "Saldo final (atual): R$ 11.000,00", "Lançamentos",
        "09/06/2026 PIX RECEBIDO + R$ " + ("10,000,00" if correct else "10,000,01"),
    ])]
    calls = []

    def extract(*args, **kwargs):
        calls.append(True)
        return pages

    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", extract)
    if correct:
        result = pdf_parser._parse_scanned_pdf_with_local_ocr_adapter(b"synthetic", on_ocr_progress=None)
        assert [row.amount for row in result.transactions] == [10000]
        assert result.parse_metrics["financial_controls_validated"] == 1
        assert result.source_page_texts == tuple(pages)
    else:
        with pytest.raises(InvalidFileContentError):
            pdf_parser._parse_scanned_pdf_with_local_ocr_adapter(b"synthetic", on_ocr_progress=None)
    assert len(calls) == 1


def test_unknown_ocr_layout_cannot_repair_a_transaction_without_financial_controls():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_ocr_text_pages(["EXTRATO DE CONTA CORRENTE\n09/06/2026 PIX RECEBIDO 10,000,00"])


def test_native_text_parsing_does_not_apply_ocr_separator_repairs():
    source = "\n".join([
        "FINANCEHUB", "Total entradas: R$ 10.000,00", "Total saídas: R$ 0,00", "Resultado: R$ 10.000,00",
        "Saldo inicial do período: R$ 1.000,00", "Saldo final (atual): R$ 11.000,00", "Lançamentos",
        "09/06/2026 PIX RECEBIDO + R$ 10,000,00",
    ])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([source])


def test_textract_table_mode_preserves_generic_amount_reading_and_original_evidence(monkeypatch):
    headers = [ExtractedCell(1, index, value) for index, value in enumerate(["Data", "Descrição", "Valor", "Saldo"], 1)]
    values = [ExtractedCell(2, index, value)
              for index, value in enumerate(["09/06/2026", "PIX RECEBIDO", "10,000,00", "11.000,00"], 1)]
    table = ExtractedTable("table", 1, 1, [headers, values])
    source = RawDocumentExtraction("aws_textract", "synthetic", [ExtractedPage(1, tables=[table])], {})
    monkeypatch.setattr(pdf_parser.TextractGateway, "__init__", lambda self: None)
    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", lambda *args, **kwargs: {
        "blocks": [], "metrics": {"textract_mode": "analysis"}, "page_count": 1,
    })
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: source)
    result = pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert [row.amount for row in result.transactions] == [10000]
    assert result.source_extraction == source
