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


def read_pdf_creation_month_year(raw_bytes: bytes) -> tuple[int, int] | None:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
    except Exception:  # pragma: no cover - best-effort metadata lookup
        return None

    metadata = reader.metadata or {}
    for field_name in ("/ModDate", "/CreationDate"):
        raw_value = str(metadata.get(field_name) or "").strip()
        parsed = _parse_pdf_metadata_month_year(raw_value)
        if parsed is not None:
            return parsed
    return None


def _parse_pdf_metadata_month_year(raw_value: str) -> tuple[int, int] | None:
    if not raw_value.startswith("D:") or len(raw_value) < 8:
        return None
    year_raw = raw_value[2:6]
    month_raw = raw_value[6:8]
    if not (year_raw.isdigit() and month_raw.isdigit()):
        return None

    year = int(year_raw)
    month = int(month_raw)
    if not 1900 <= year <= 2100:
        return None
    if not 1 <= month <= 12:
        return None
    return month, year
