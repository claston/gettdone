from io import BytesIO

from pypdf import PdfReader

from app.application.errors import InvalidFileContentError


def read_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc

    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return [item for item in pages if item]


def read_layout_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc

    pages = [(page.extract_text(extraction_mode="layout") or "").strip() for page in reader.pages]
    return [item for item in pages if item]


def read_pdf_page_count(raw_bytes: bytes) -> int:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        return len(reader.pages)
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc
