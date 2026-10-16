import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application import pdf_parser
from app.application.ai_recovery.shadow_dispatch import _source_evidence
from app.application.conversion.document_extractor import LegacyParsingServiceDocumentExtractor
from app.application.conversion.statement_parser import LegacyExtractedDocumentStatementParser, resolve_legacy_parsed_statement
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.document_extraction_models import ExtractedCell, ExtractedLine, ExtractedPage
from app.application.errors import OcrFinancialAmbiguityError
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.parsers.pdf.models import PdfParseResult
from app.application.parsers.service import ParsingService
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction
from app.application.textract_transaction_adapter import TextractTransactionExtractionResult


@pytest.fixture
def basic_ocr_response():
    path = Path(__file__).parent / "fixtures" / "ocr" / "printed_credit_with_handwritten_note.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_basic_ocr_preserves_printed_value_and_handwritten_annotation(basic_ocr_response):
    extraction = map_textract_blocks_to_extraction(
        document_hash="synthetic", blocks=basic_ocr_response["blocks"], page_count=2,
    )

    first_page, annex = extraction.pages
    words = {word.id: word for word in first_page.words}
    assert first_page.lines[0].text == "05/10/2026 PIX RECEBIDO 350,00 - NOTA12"
    assert first_page.lines[0].word_ids == ("date", "description-1", "description-2", "credit", "dash", "note")
    assert words["credit"].text == "350,00"
    assert words["credit"].text_type == "PRINTED"
    assert words["credit"].confidence == 98.4
    assert words["credit"].bbox == {"left": 0.6, "top": 0.3, "width": 0.06, "height": 0.02}
    assert words["credit"].polygon[0] == {"x": 0.6, "y": 0.3}
    assert words["dash"].text == "-"
    assert words["dash"].text_type == "HANDWRITING"
    assert words["dash"].confidence == 74.0
    # A printed label does not imply that a word is safe to interpret as financial data.
    assert words["note"].text_type == "PRINTED"
    assert annex.words[0].page_number == 2
    assert annex.words[0].word_index == 1
    assert extraction.metrics["word_count"] == 7
    assert extraction.metrics["handwriting_word_count"] == 1
    assert json.loads(json.dumps(asdict(extraction)))["pages"][0]["words"][4]["text_type"] == "HANDWRITING"


def test_word_evidence_is_optional_for_existing_extraction_models():
    line = ExtractedLine("old-line", 1, 1, "legacy text")
    cell = ExtractedCell(1, 1, "legacy cell")
    page = ExtractedPage(1)
    assert line.word_ids == ()
    assert cell.word_ids == ()
    assert page.words == []


def test_mapper_keeps_words_without_lines_and_empty_pages():
    extraction = map_textract_blocks_to_extraction(document_hash="synthetic", blocks=[
        {"BlockType": "PAGE", "Id": "page-1", "Page": 1},
        {"BlockType": "WORD", "Id": "word-2", "Page": 2, "Text": "unknown"},
        {"BlockType": "PAGE", "Id": "page-3", "Page": 3},
    ])
    assert [page.page_number for page in extraction.pages] == [1, 2, 3]
    assert extraction.pages[0].words == []
    word = extraction.pages[1].words[0]
    assert word.text_type is None
    assert word.confidence is None
    assert word.bbox is None
    assert word.polygon is None
    assert extraction.pages[2].lines == []


def test_mapper_links_words_to_cells_and_inherits_parent_page():
    extraction = map_textract_blocks_to_extraction(document_hash="synthetic", blocks=[
        {"BlockType": "WORD", "Id": "credit", "Text": "350,00", "TextType": "PRINTED"},
        {"BlockType": "WORD", "Id": "dash", "Text": "-", "TextType": "HANDWRITING"},
        {"BlockType": "CELL", "Id": "cell", "RowIndex": 2, "ColumnIndex": 3,
         "Relationships": [{"Type": "CHILD", "Ids": ["credit", "dash", "missing"]}]},
        {"BlockType": "TABLE", "Id": "table", "Page": 2,
         "Relationships": [{"Type": "CHILD", "Ids": ["cell"]}]},
        {"BlockType": "LINE", "Id": "line", "Page": 2, "Text": "350,00 -",
         "Relationships": [{"Type": "CHILD", "Ids": ["credit", "dash", "missing", "cell"]}]},
    ], page_count=2)
    page = extraction.pages[1]
    assert [word.id for word in page.words] == ["credit", "dash"]
    assert all(word.page_number == 2 for word in page.words)
    assert page.lines[0].word_ids == ("credit", "dash")
    cell = page.tables[0].rows[1][2]
    assert cell.text == "350,00 -"
    assert cell.id == "cell"
    assert cell.word_ids == ("credit", "dash")


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_parser_and_legacy_bridge_preserve_evidence_without_another_ocr(monkeypatch, basic_ocr_response, mode):
    gateway = SimpleNamespace(analyze_pdf=Mock(return_value={
        "job_id": "synthetic-job", "document_hash": "synthetic", "page_count": 2,
        "blocks": basic_ocr_response["blocks"], "metrics": {"textract_mode": mode},
    }))
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: gateway)
    transaction = NormalizedTransaction(date="2026-10-05", description="PIX RECEBIDO", amount=350.0, type="inflow")
    canonical = CanonicalTransaction(date="2026-10-05", description="PIX RECEBIDO", amount=350.0, type="inflow")
    layout = PdfLayoutInference(layout_name="generic_statement_ptbr", confidence=0.25, used_fallback=True)
    if mode == "text":
        monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_text_pages", lambda extraction: PdfParseResult(
            transactions=[transaction], canonical_transactions=[canonical], layout=layout,
            extracted_text="synthetic", parse_metrics={}, source_page_texts=("synthetic", "annex"),
        ))
    else:
        monkeypatch.setattr(pdf_parser, "adapt_textract_extraction_to_transactions", lambda extraction: TextractTransactionExtractionResult(
            transactions=[transaction], canonical_transactions=[canonical], extracted_text="synthetic", parse_metrics={},
        ))
    result = pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic-pdf")
    extraction = result.source_extraction
    assert extraction.operation_id == "synthetic-job"
    assert extraction.pages[0].words[4].text_type == "HANDWRITING"
    # Observability wrapping must not drop the already-paid extraction.
    result = pdf_parser._with_parse_observability(result, native_text_detected=0)
    document = UploadedDocument(filename="synthetic.pdf", raw_bytes=b"synthetic-pdf", file_type="pdf")
    extractor = LegacyParsingServiceDocumentExtractor(parsing_service=ParsingService())
    extracted = extractor.extract(document=document, pdf_parser=lambda raw_bytes, **kwargs: result)
    assert extracted.source_extraction is extraction
    statement = LegacyExtractedDocumentStatementParser().parse(extracted_document=extracted)
    assert statement.source_extraction is extraction
    assert resolve_legacy_parsed_statement(statement).source_extraction is extraction
    gateway.analyze_pdf.assert_called_once_with(raw_bytes=b"synthetic-pdf")
    evidence = json.loads(json.dumps(_source_evidence(
        result.source_page_texts, result.source_layout_lines, extraction,
    )))
    assert evidence["classification"] == "restricted"
    assert evidence["extraction"]["operation_id"] == "synthetic-job"
    assert evidence["pages"][0]["words"][4]["text_type"] == "HANDWRITING"
    assert evidence["pages"][0]["lines"][0]["word_ids"][-2:] == ["dash", "note"]


def test_financial_ambiguity_does_not_repeat_ocr_with_a_different_parser(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw: [])
    monkeypatch.setattr(pdf_parser, "is_pdf_ocr_enabled", lambda: True)
    gateway = Mock(side_effect=OcrFinancialAmbiguityError("Review required"))
    local_ocr = Mock()
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_local_ocr_adapter", local_ocr)
    with pytest.raises(OcrFinancialAmbiguityError, match="Review required"):
        pdf_parser.parse_pdf_transactions(b"synthetic-pdf")
    gateway.assert_called_once()
    local_ocr.assert_not_called()
