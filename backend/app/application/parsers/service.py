from dataclasses import dataclass, replace
from typing import Callable

from app.application.canonization import build_transaction_metadata
from app.application.ingestion import IngestedDocument
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.parsers.csv import parse_csv_transactions
from app.application.parsers.ofx import parse_ofx_transactions
from app.application.parsers.xlsx import parse_xlsx_transactions
from app.application.pdf_parser import parse_pdf_transactions

OcrProgressCallback = Callable[[int, int], None]
PdfParser = Callable[..., object]


@dataclass(frozen=True)
class ParsedDocument:
    file_type: str
    transactions: list[NormalizedTransaction]
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    extracted_text: str | None = None
    parse_metrics: dict[str, int | float | str] | None = None
    canonical_transactions: list[CanonicalTransaction] | None = None
    warning_types: list[list[str]] | None = None
    running_balances: list[float | None] | None = None


class ParsingService:
    def parse(
        self,
        document: IngestedDocument,
        *,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser | None = None,
    ) -> ParsedDocument:
        if document.file_type == "csv":
            return self._with_transaction_metadata(
                ParsedDocument(
                    file_type=document.file_type,
                    transactions=parse_csv_transactions(document.raw_bytes),
                )
            )
        if document.file_type == "xlsx":
            return self._with_transaction_metadata(
                ParsedDocument(
                    file_type=document.file_type,
                    transactions=parse_xlsx_transactions(document.raw_bytes),
                )
            )
        if document.file_type == "ofx":
            return self._with_transaction_metadata(
                ParsedDocument(
                    file_type=document.file_type,
                    transactions=parse_ofx_transactions(document.raw_bytes),
                )
            )
        return self._parse_pdf(
            document,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            pdf_parser=pdf_parser or parse_pdf_transactions,
        )

    def _parse_pdf(
        self,
        document: IngestedDocument,
        *,
        on_ocr_progress: OcrProgressCallback | None,
        max_ocr_pages: int | None,
        pdf_parser: PdfParser,
    ) -> ParsedDocument:
        if on_ocr_progress is None:
            try:
                result = pdf_parser(document.raw_bytes, max_ocr_pages=max_ocr_pages)
            except TypeError as exc:
                if "max_ocr_pages" not in str(exc):
                    raise
                result = pdf_parser(document.raw_bytes)
        else:
            try:
                result = pdf_parser(
                    document.raw_bytes,
                    on_ocr_progress=on_ocr_progress,
                    max_ocr_pages=max_ocr_pages,
                )
            except TypeError as exc:
                if "max_ocr_pages" not in str(exc):
                    raise
                result = pdf_parser(document.raw_bytes, on_ocr_progress=on_ocr_progress)

        return self._with_transaction_metadata(
            ParsedDocument(
                file_type=document.file_type,
                transactions=result.transactions,
                layout_inference_name=result.layout.layout_name,
                layout_inference_confidence=result.layout.confidence,
                extracted_text=result.extracted_text,
                parse_metrics=result.parse_metrics,
                canonical_transactions=result.canonical_transactions,
            )
        )

    def _with_transaction_metadata(self, parsed_document: ParsedDocument) -> ParsedDocument:
        metadata = build_transaction_metadata(parsed_document)
        return replace(
            parsed_document,
            warning_types=metadata.warning_types,
            running_balances=metadata.running_balances,
        )
