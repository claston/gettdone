import sys
from types import SimpleNamespace

import pytest

from app.application import pdf_ocr
from app.application.errors import InvalidFileContentError


def test_first_page_header_ocr_crops_page_and_ignores_total_document_page_limit(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeImage:
        width = 800
        height = 1_000

        def crop(self, box):
            observed["crop_box"] = box
            cropped = FakeImage()
            cropped.height = box[3]
            return cropped

        def close(self) -> None:
            pass

    class FakeBitmap:
        def to_pil(self):
            return FakeImage()

        def close(self) -> None:
            pass

    class FakePage:
        def render(self, *, scale: float):
            observed["render_scale"] = scale
            return FakeBitmap()

        def close(self) -> None:
            pass

    class FakeDocument:
        def __len__(self) -> int:
            return 50

        def __getitem__(self, page_index: int):
            observed["page_index"] = page_index
            return FakePage()

        def close(self) -> None:
            observed["document_closed"] = True

    class FakeSemaphore:
        def release(self) -> None:
            observed["semaphore_released"] = True

    monkeypatch.setattr(pdf_ocr, "is_pdf_ocr_enabled", lambda: False)
    monkeypatch.setattr(
        pdf_ocr,
        "_enforce_pdf_ocr_file_size_limit",
        lambda _raw_bytes: pytest.fail("header-only OCR must not use the full-document OCR size limit"),
    )
    monkeypatch.setattr(pdf_ocr, "_acquire_ocr_slot_or_raise", lambda: None)
    monkeypatch.setattr(pdf_ocr, "_get_ocr_semaphore", lambda: FakeSemaphore())
    monkeypatch.setattr(pdf_ocr, "_configure_tesseract_command", lambda _pytesseract: None)
    monkeypatch.setattr(pdf_ocr, "_get_pdf_ocr_max_pages", lambda: 1)
    monkeypatch.setattr(pdf_ocr, "_get_pdf_ocr_render_dpi", lambda: 216)
    monkeypatch.setattr(
        pdf_ocr,
        "_image_to_string_with_timeout",
        lambda _pytesseract, *, image, lang, timeout_seconds: (
            observed.update(
                {
                    "ocr_image_height": image.height,
                    "ocr_lang": lang,
                    "ocr_timeout": timeout_seconds,
                }
            )
            or "SANTANDER"
        ),
    )
    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=lambda _raw_bytes: FakeDocument()))
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace())

    text = pdf_ocr.extract_pdf_first_page_header_text_with_ocr(b"%PDF synthetic")

    assert text == "SANTANDER"
    assert observed["page_index"] == 0
    assert observed["crop_box"] == (0, 0, 800, 350)
    assert observed["ocr_image_height"] == 350
    assert observed["render_scale"] == 3
    assert observed["document_closed"] is True
    assert observed["semaphore_released"] is True


def test_first_page_header_ocr_rejects_source_above_upload_limit() -> None:
    with pytest.raises(InvalidFileContentError, match="first-page header OCR supports files up to"):
        pdf_ocr.extract_pdf_first_page_header_text_with_ocr(b"x" * ((10 * 1024 * 1024) + 1))
