import logging
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.application.errors import FileTooLargeError, MaxPagesPerFileExceededError

logger = logging.getLogger(__name__)

TEXT_PDF_MAX_PAGES_PER_FILE = 250
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
OCR_PDF_MAX_PAGES_PER_FILE = 10
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
OCR_CONTEXT_SCANNED_PDF = "scanned_pdf"
OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK = "unidentified_model_fallback"


@dataclass(frozen=True, slots=True)
class DocumentPreflightResult:
    scanned_likely: bool
    estimated_pages_count: int | None


@dataclass(frozen=True, slots=True)
class DocumentPreflightPolicy:
    max_upload_size_bytes: int
    max_pages_per_file: int | None
    ocr_max_pages: int


class DocumentPreflightService:
    def inspect_raw_bytes(self, *, filename: str, raw_bytes: bytes) -> DocumentPreflightResult:
        if Path(filename or "").suffix.lower() != ".pdf":
            return DocumentPreflightResult(scanned_likely=False, estimated_pages_count=None)
        try:
            reader = PdfReader(BytesIO(raw_bytes))
            return _build_pdf_preflight_result(reader)
        except Exception:
            return DocumentPreflightResult(scanned_likely=False, estimated_pages_count=None)

    def inspect_staged_upload(self, *, filename: str, staged_path: Path) -> DocumentPreflightResult:
        if Path(filename or "").suffix.lower() != ".pdf":
            return DocumentPreflightResult(scanned_likely=False, estimated_pages_count=None)
        try:
            reader = PdfReader(str(staged_path))
            return _build_pdf_preflight_result(reader)
        except Exception:
            return DocumentPreflightResult(scanned_likely=False, estimated_pages_count=None)

    def build_policy(
        self,
        *,
        identity,
        filename: str,
        staged_upload_size_bytes: int,
        preflight_result: DocumentPreflightResult,
    ) -> DocumentPreflightPolicy:
        estimated_pages_count = preflight_result.estimated_pages_count
        scanned_likely = preflight_result.scanned_likely
        max_pages_per_file: int | None = None

        if Path(filename or "").suffix.lower() == ".pdf" and estimated_pages_count is not None:
            max_pages_per_file = self.resolve_max_pages_per_file(
                identity=identity,
                scanned_likely=scanned_likely,
            )
            if int(estimated_pages_count) > max_pages_per_file:
                self._log_pages_limit_exceeded_attempt(
                    identity=identity,
                    filename=filename,
                    pages_count=int(estimated_pages_count),
                    max_pages_per_file=max_pages_per_file,
                    scanned_likely=scanned_likely,
                )
                raise MaxPagesPerFileExceededError(
                    pages_count=int(estimated_pages_count),
                    max_pages_per_file=max_pages_per_file,
                    ocr_context=OCR_CONTEXT_SCANNED_PDF if scanned_likely is True else None,
                )

        max_upload_size_bytes = self.resolve_max_upload_size_bytes(
            identity=identity,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
        )
        if staged_upload_size_bytes > max_upload_size_bytes:
            raise self._build_file_too_large_error(max_upload_size_bytes)

        return DocumentPreflightPolicy(
            max_upload_size_bytes=max_upload_size_bytes,
            max_pages_per_file=max_pages_per_file,
            ocr_max_pages=self.resolve_ocr_max_pages_per_file(identity),
        )

    def resolve_max_pages_per_file(self, *, identity, scanned_likely: bool | None) -> int:
        identity_max_pages = max(1, int(getattr(identity, "max_pages_per_file", 10**9)))
        if scanned_likely is True:
            return self.resolve_ocr_max_pages_per_file(identity)
        if scanned_likely is False:
            return max(identity_max_pages, TEXT_PDF_MAX_PAGES_PER_FILE)
        return identity_max_pages

    def resolve_ocr_max_pages_per_file(self, identity) -> int:
        identity_max_pages = max(1, int(getattr(identity, "max_pages_per_file", 10**9)))
        identity_ocr_pages = max(
            1,
            int(getattr(identity, "max_pages_per_file_ocr", OCR_PDF_MAX_PAGES_PER_FILE) or OCR_PDF_MAX_PAGES_PER_FILE),
        )
        return min(identity_max_pages, identity_ocr_pages, self._resolve_pdf_ocr_env_max_pages())

    def resolve_max_upload_size_bytes(
        self,
        *,
        identity,
        scanned_likely: bool | None,
        estimated_pages_count: int | None,
    ) -> int:
        identity_max_bytes = max(1, int(getattr(identity, "max_upload_size_bytes", 10**9)))
        if scanned_likely is True:
            return min(identity_max_bytes, OCR_PDF_MAX_UPLOAD_SIZE_BYTES)
        if scanned_likely is False and estimated_pages_count is not None:
            return max(identity_max_bytes, TEXT_PDF_MAX_UPLOAD_SIZE_BYTES)
        return identity_max_bytes

    def resolve_ocr_limit_context(self, *, scanned_likely: bool | None) -> str:
        if scanned_likely is True:
            return OCR_CONTEXT_SCANNED_PDF
        return OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK

    def _resolve_pdf_ocr_env_max_pages(self) -> int:
        raw = os.getenv("PDF_OCR_MAX_PAGES", "").strip()
        if not raw:
            return 12
        try:
            value = int(raw)
        except ValueError:
            return 12
        return max(1, value)

    def _build_file_too_large_error(self, max_upload_size_bytes: int) -> FileTooLargeError:
        exc = FileTooLargeError()
        setattr(exc, "_max_upload_size_bytes", int(max_upload_size_bytes))
        return exc

    def _log_pages_limit_exceeded_attempt(
        self,
        *,
        identity,
        filename: str,
        pages_count: int,
        max_pages_per_file: int,
        scanned_likely: bool | None,
    ) -> None:
        logger.info(
            (
                "conversion_pages_limit_exceeded identity_type=%s identity_id=%s filename=%s "
                "pages_count=%s max_pages_per_file=%s scanned_likely=%s"
            ),
            getattr(identity, "identity_type", "unknown"),
            getattr(identity, "identity_id", "unknown"),
            filename,
            pages_count,
            max_pages_per_file,
            scanned_likely,
        )


def _build_pdf_preflight_result(reader: PdfReader) -> DocumentPreflightResult:
    total_pages = len(reader.pages)
    extracted_chars = 0
    for page in reader.pages:
        extracted_chars += len((page.extract_text() or "").strip())
    return DocumentPreflightResult(
        scanned_likely=extracted_chars < 40,
        estimated_pages_count=total_pages,
    )
