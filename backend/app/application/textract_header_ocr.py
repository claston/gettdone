from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.application.pdf_ocr import _get_bank_header_ocr_crop_ratio
from app.application.textract_gateway import TextractGateway


def extract_pdf_first_page_header_text_with_textract(raw_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(raw_bytes))
    if not reader.pages:
        return ""

    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    first_page_pdf = BytesIO()
    writer.write(first_page_pdf)

    result = TextractGateway(mode="text", timeout_seconds=60.0).analyze_pdf(
        raw_bytes=first_page_pdf.getvalue()
    )
    header_ratio = _get_bank_header_ocr_crop_ratio()
    lines: list[tuple[float, float, str]] = []
    for block in result.get("blocks") or []:
        if block.get("BlockType") != "LINE" or block.get("Page", 1) != 1:
            continue
        bbox = (block.get("Geometry") or {}).get("BoundingBox") or {}
        top = bbox.get("Top")
        if not isinstance(top, int | float) or top >= header_ratio:
            continue
        text = str(block.get("Text") or "").strip()
        if not text:
            continue
        left = bbox.get("Left")
        lines.append((float(top), float(left) if isinstance(left, int | float) else 0.0, text))
    return "\n".join(text for _, _, text in sorted(lines))
