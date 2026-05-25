import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import BoundedSemaphore
from typing import Callable

from app.application.errors import InvalidFileContentError

PDF_OCR_DISABLED_MESSAGE = (
    "PDF does not contain extractable text. OCR fallback is disabled for this release."
)
_OCR_SEMAPHORE: BoundedSemaphore | None = None


def is_pdf_ocr_enabled() -> bool:
    raw = os.getenv("PDF_OCR_ENABLED", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return _is_dev_auto_ocr_enabled()


def extract_pdf_page_texts_with_ocr(
    raw_bytes: bytes,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    if not is_pdf_ocr_enabled():
        raise InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE)
    _enforce_pdf_ocr_file_size_limit(raw_bytes)
    _acquire_ocr_slot_or_raise()

    engine = _resolve_pdf_ocr_engine()
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise InvalidFileContentError(
            "OCR dependencies are not installed. Install optional packages for OCR support."
        ) from exc

    pytesseract = None
    paddle_ocr = None
    if engine == "tesseract":
        try:
            import pytesseract
        except Exception as exc:
            raise InvalidFileContentError(
                "Tesseract OCR dependencies are not installed."
            ) from exc
        _configure_tesseract_command(pytesseract)
    else:
        _configure_paddle_cache_dir()
        try:
            from paddleocr import PaddleOCR
        except Exception as exc:
            raise InvalidFileContentError(
                f"PaddleOCR initialization failed: {exc}"
            ) from exc
        paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en")

    ocr_lang = _resolve_ocr_lang()
    page_timeout_seconds = _get_pdf_ocr_page_timeout_seconds()

    document = None
    try:
        document = pdfium.PdfDocument(raw_bytes)
    except Exception as exc:
        raise InvalidFileContentError("Unable to render PDF pages for OCR.") from exc

    try:
        max_pages = _get_pdf_ocr_max_pages()
        if len(document) > max_pages:
            raise InvalidFileContentError(
                f"OCR fallback is limited to {max_pages} pages to protect memory usage. "
                "Try a smaller PDF or disable OCR fallback."
            )

        texts: list[str] = []
        render_dpi = _get_pdf_ocr_render_dpi()
        total_pages = len(document)
        for page_index in range(total_pages):
            page = None
            bitmap = None
            image = None
            try:
                page = document[page_index]
                bitmap = page.render(scale=render_dpi / 72)
                image = bitmap.to_pil()
                if engine == "tesseract":
                    text = (
                        _image_to_string_with_timeout(
                            pytesseract,
                            image=image,
                            lang=ocr_lang,
                            timeout_seconds=page_timeout_seconds,
                        )
                        or ""
                    ).strip()
                else:
                    text = (
                        _image_to_string_with_paddle_timeout(
                            paddle_ocr,
                            image=image,
                            timeout_seconds=page_timeout_seconds,
                        )
                        or ""
                    ).strip()
                if on_progress is not None:
                    on_progress(page_index + 1, total_pages)
                if text:
                    texts.append(text)
            except Exception as exc:
                raise InvalidFileContentError(f"OCR failed while processing PDF pages: {exc}") from exc
            finally:
                try:
                    image.close()
                except Exception:
                    pass
                try:
                    bitmap.close()
                except Exception:
                    pass
                try:
                    page.close()
                except Exception:
                    pass
        return texts
    finally:
        try:
            document.close()
        except Exception:
            pass
        _get_ocr_semaphore().release()


def _get_pdf_ocr_max_pages() -> int:
    raw = os.getenv("PDF_OCR_MAX_PAGES", "").strip()
    if not raw:
        return 12
    try:
        value = int(raw)
    except ValueError:
        return 12
    return max(1, value)


def _get_pdf_ocr_render_dpi() -> int:
    raw = os.getenv("PDF_OCR_DPI", "").strip()
    if not raw:
        return 250
    try:
        value = int(raw)
    except ValueError:
        return 250
    return max(150, min(400, value))


def _get_pdf_ocr_page_timeout_seconds() -> float:
    raw = os.getenv("PDF_OCR_PAGE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 12.0
    try:
        value = float(raw)
    except ValueError:
        return 12.0
    return max(3.0, min(60.0, value))


def _resolve_ocr_lang() -> str:
    raw = os.getenv("PDF_OCR_LANG", "").strip()
    if raw:
        return raw
    return "por+eng"


def _resolve_pdf_ocr_engine() -> str:
    raw = os.getenv("PDF_OCR_ENGINE", "").strip().lower()
    if raw in {"", "tesseract"}:
        return "tesseract"
    if raw in {"paddle", "paddleocr"}:
        return "paddle"
    return "tesseract"


def _image_to_string_with_lang_fallback(pytesseract, *, image, lang: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=lang)
    except Exception as exc:
        if lang != "eng" and _is_missing_tesseract_language_error(str(exc)):
            return pytesseract.image_to_string(image, lang="eng")
        raise


def _image_to_string_with_timeout(pytesseract, *, image, lang: str, timeout_seconds: float) -> str:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_image_to_string_with_lang_fallback, pytesseract, image=image, lang=lang)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            raise InvalidFileContentError(
                f"OCR timeout after {timeout_seconds:.0f}s on one page. Try a smaller or clearer PDF."
            ) from exc


def _image_to_string_with_paddle(paddle_ocr, *, image) -> str:
    try:
        import numpy as np
    except Exception as exc:
        raise InvalidFileContentError("PaddleOCR requires numpy.") from exc

    np_image = np.array(image.convert("RGB"))
    result = paddle_ocr.ocr(np_image)
    if not result:
        return ""

    lines: list[str] = []
    for block in result:
        if not block:
            continue
        for item in block:
            if len(item) < 2 or not item[1]:
                continue
            text = str(item[1][0] or "").strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _image_to_string_with_paddle_timeout(paddle_ocr, *, image, timeout_seconds: float) -> str:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_image_to_string_with_paddle, paddle_ocr, image=image)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            raise InvalidFileContentError(
                f"OCR timeout after {timeout_seconds:.0f}s on one page. Try a smaller or clearer PDF."
            ) from exc


def _is_missing_tesseract_language_error(message: str) -> bool:
    normalized = (message or "").lower()
    return "failed loading language" in normalized or "could not initialize tesseract" in normalized


def _configure_tesseract_command(pytesseract) -> None:
    _configure_tessdata_prefix()

    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
        return

    current = (getattr(pytesseract.pytesseract, "tesseract_cmd", "") or "").strip()
    if current and Path(current).exists():
        return

    candidates = [
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return


def _configure_tessdata_prefix() -> None:
    configured = os.getenv("TESSDATA_PREFIX", "").strip()
    if configured:
        return
    local_tessdata = _resolve_local_tessdata_dir()
    if local_tessdata.exists():
        os.environ["TESSDATA_PREFIX"] = str(local_tessdata) + os.sep


def _is_dev_auto_ocr_enabled() -> bool:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env == "production":
        return False
    return _resolve_local_tessdata_dir().exists() and _find_default_tesseract_cmd() is not None


def _acquire_ocr_slot_or_raise() -> None:
    semaphore = _get_ocr_semaphore()
    acquired = semaphore.acquire(blocking=False)
    if not acquired:
        raise InvalidFileContentError("OCR is busy. Please retry in a few seconds.")


def _enforce_pdf_ocr_file_size_limit(raw_bytes: bytes) -> None:
    max_bytes = _get_pdf_ocr_max_file_bytes()
    if len(raw_bytes) <= max_bytes:
        return
    max_mb = round(max_bytes / (1024 * 1024), 1)
    raise InvalidFileContentError(f"OCR supports files up to {max_mb} MB in this environment.")


def _get_pdf_ocr_max_file_bytes() -> int:
    raw = os.getenv("PDF_OCR_MAX_FILE_MB", "").strip()
    if not raw:
        return 5 * 1024 * 1024
    try:
        mb = float(raw)
    except ValueError:
        return 5 * 1024 * 1024
    safe_mb = max(1.0, min(25.0, mb))
    return int(safe_mb * 1024 * 1024)


def _get_ocr_semaphore() -> BoundedSemaphore:
    global _OCR_SEMAPHORE
    if _OCR_SEMAPHORE is None:
        _OCR_SEMAPHORE = BoundedSemaphore(value=_get_pdf_ocr_concurrency_limit())
    return _OCR_SEMAPHORE


def _get_pdf_ocr_concurrency_limit() -> int:
    raw = os.getenv("PDF_OCR_CONCURRENCY_LIMIT", "").strip()
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, min(4, value))


def _resolve_local_tessdata_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tmp" / "tessdata"


def _configure_paddle_cache_dir() -> None:
    cache_dir = Path(__file__).resolve().parents[2] / "tmp" / "paddlex"
    cache_dir.mkdir(parents=True, exist_ok=True)
    home_dir = cache_dir / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
    os.environ["PADDLE_HOME"] = str(cache_dir / "paddle_home")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir / "xdg_cache")
    os.environ["HOME"] = str(home_dir)
    os.environ["USERPROFILE"] = str(home_dir)


def _find_default_tesseract_cmd() -> Path | None:
    candidates = [
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

