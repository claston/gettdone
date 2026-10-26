from collections.abc import Mapping
from io import BytesIO

from pypdf.generic import NullObject

from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.reader import open_pdf_reader


def read_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = open_pdf_reader(BytesIO(raw_bytes))
    except InvalidFileContentError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise _build_native_text_error(
            "Unable to read PDF bytes.",
            exc=exc,
            pdf_structure_read_ok=False,
        ) from exc

    try:
        pages = []
        for page in reader.pages:
            validate_native_pdf_fonts(page)
            pages.append((page.extract_text() or "").strip())
    except Exception as exc:
        raise _build_native_text_error(
            "Unable to extract native PDF text.",
            exc=exc,
            pdf_structure_read_ok=True,
        ) from exc
    return [item for item in pages if item]


def read_layout_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = open_pdf_reader(BytesIO(raw_bytes))
    except InvalidFileContentError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise _build_native_text_error(
            "Unable to read PDF bytes.",
            exc=exc,
            pdf_structure_read_ok=False,
        ) from exc

    try:
        pages = []
        for page in reader.pages:
            validate_native_pdf_fonts(page)
            pages.append((page.extract_text(extraction_mode="layout") or "").strip())
    except Exception as exc:
        raise _build_native_text_error(
            "Unable to extract native PDF text in layout mode.",
            exc=exc,
            pdf_structure_read_ok=True,
        ) from exc
    return [item for item in pages if item]


def validate_native_pdf_fonts(page) -> None:
    # PyPDF 6 can silently return incomplete text for a null font; preserve the
    # extraction-failure signal used by preflight and the OCR retry policy.
    if not isinstance(page, Mapping):
        return
    resources = page.get("/Resources")
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    fonts = resources.get("/Font") if isinstance(resources, Mapping) else None
    if hasattr(fonts, "get_object"):
        fonts = fonts.get_object()
    if isinstance(fonts, Mapping):
        for font in fonts.values():
            if hasattr(font, "get_object"):
                font = font.get_object()
            if font is None or isinstance(font, NullObject):
                raise TypeError("PDF page contains a null font resource.")


def _build_native_text_error(
    message: str,
    *,
    exc: Exception,
    pdf_structure_read_ok: bool,
) -> InvalidFileContentError:
    error = InvalidFileContentError(message)
    setattr(error, "_pdf_structure_read_ok", pdf_structure_read_ok)
    setattr(error, "_native_text_extraction_ok", False)
    setattr(error, "_native_text_error_type", exc.__class__.__name__)
    return error


def read_pdf_page_count(raw_bytes: bytes) -> int:
    try:
        reader = open_pdf_reader(BytesIO(raw_bytes))
        return len(reader.pages)
    except InvalidFileContentError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc


def read_pdf_creation_month_year(raw_bytes: bytes) -> tuple[int, int] | None:
    try:
        reader = open_pdf_reader(BytesIO(raw_bytes))
        metadata = reader.metadata or {}
    except InvalidFileContentError:
        raise
    except Exception:  # pragma: no cover - best-effort metadata lookup
        return None

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
