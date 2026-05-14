import os

from app.application.errors import InvalidFileContentError

PDF_OCR_DISABLED_MESSAGE = (
    "PDF does not contain extractable text. OCR fallback is disabled for this release."
)


def is_pdf_ocr_enabled() -> bool:
    """OCR code is retained for future hardening, but runtime execution is disabled."""
    return False


def extract_pdf_page_texts_with_ocr(raw_bytes: bytes) -> list[str]:
    if not is_pdf_ocr_enabled():
        raise InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE)

    try:
        import pypdfium2 as pdfium
        import pytesseract
    except Exception as exc:
        raise InvalidFileContentError(
            "OCR dependencies are not installed. Install optional packages for OCR support."
        ) from exc

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
        for page_index in range(len(document)):
            page = None
            bitmap = None
            image = None
            try:
                page = document[page_index]
                bitmap = page.render(scale=200 / 72)
                image = bitmap.to_pil()
                text = (pytesseract.image_to_string(image, lang="por+eng") or "").strip()
                if text:
                    texts.append(text)
            except Exception as exc:
                raise InvalidFileContentError("OCR failed while processing PDF pages.") from exc
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


def _get_pdf_ocr_max_pages() -> int:
    raw = os.getenv("PDF_OCR_MAX_PAGES", "").strip()
    if not raw:
        return 12
    try:
        value = int(raw)
    except ValueError:
        return 12
    return max(1, value)

