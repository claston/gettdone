from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.application.ingestion import IngestedDocument
from app.application.parsers.service import ParsedDocument, ParsingService
from app.application.pdf_parser import parse_pdf_transactions

OcrProgressCallback = Callable[[int, int], None]
PdfParser = Callable[..., object]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    source_document: IngestedDocument
    extracted_text: str | None = None
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    metadata: dict[str, Any] | None = None


class DocumentExtractor(Protocol):
    def extract(
        self,
        *,
        document: IngestedDocument,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser = parse_pdf_transactions,
    ) -> ExtractedDocument: ...


class LegacyParsingServiceDocumentExtractor:
    """Transitional adapter over the legacy parser-backed extraction flow."""

    def __init__(self, *, parsing_service: ParsingService | None = None) -> None:
        self.parsing_service = parsing_service or ParsingService()

    def extract(
        self,
        *,
        document: IngestedDocument,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser = parse_pdf_transactions,
    ) -> ExtractedDocument:
        parsed_document = self.parsing_service.parse(
            document,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            pdf_parser=pdf_parser,
        )
        return ExtractedDocument(
            source_document=document,
            extracted_text=parsed_document.extracted_text,
            layout_inference_name=parsed_document.layout_inference_name,
            layout_inference_confidence=parsed_document.layout_inference_confidence,
            metadata={
                "legacy_parsed_document": parsed_document,
            },
        )


def resolve_legacy_parsed_document(extracted_document: ExtractedDocument) -> ParsedDocument | None:
    metadata = extracted_document.metadata or {}
    legacy_parsed_document = metadata.get("legacy_parsed_document")
    if isinstance(legacy_parsed_document, ParsedDocument):
        return legacy_parsed_document
    return None
