from app.application import pdf_parser
from app.application.models import NormalizedTransaction
from app.application.parsers.pdf import (
    PdfParseResult,
    _ParsedTransaction,
    _PdfLine,
    _TabularColumnPositions,
    text_extraction,
)
from app.application.pdf_layout_inference import PdfLayoutInference


def test_pdf_parser_facade_reexports_package_models() -> None:
    assert pdf_parser.PdfParseResult is PdfParseResult
    assert pdf_parser._PdfLine is _PdfLine
    assert pdf_parser._ParsedTransaction is _ParsedTransaction
    assert pdf_parser._TabularColumnPositions is _TabularColumnPositions


def test_package_pdf_parse_result_is_compatible_with_legacy_import() -> None:
    result = PdfParseResult(
        transactions=[
            NormalizedTransaction(
                date="2026-06-18",
                description="PIX RECEBIDO",
                amount=10.0,
                type="credit",
            )
        ],
        layout=PdfLayoutInference(
            layout_name="generic_statement_ptbr",
            confidence=0.5,
            used_fallback=True,
        ),
        extracted_text="18/06 PIX RECEBIDO 10,00",
        parse_metrics={"selected_parser": "inline"},
    )

    assert isinstance(result, pdf_parser.PdfParseResult)
    assert result.transactions[0].description == "PIX RECEBIDO"


def test_pdf_parser_legacy_native_reader_wrappers_delegate_to_package(monkeypatch) -> None:
    monkeypatch.setattr(text_extraction, "read_native_pdf_page_texts", lambda raw_bytes: ["native text"])
    monkeypatch.setattr(text_extraction, "read_layout_native_pdf_page_texts", lambda raw_bytes: ["layout text"])
    monkeypatch.setattr(text_extraction, "read_pdf_page_count", lambda raw_bytes: 7)

    assert pdf_parser._read_native_pdf_page_texts(b"pdf") == ["native text"]
    assert pdf_parser._read_layout_native_pdf_page_texts(b"pdf") == ["layout text"]
    assert pdf_parser._read_pdf_page_count(b"pdf") == 7
