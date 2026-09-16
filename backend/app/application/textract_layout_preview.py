from __future__ import annotations

from app.application.document_extraction_models import ExtractedLine
from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction
from app.application.textract_gateway import TextractGateway


def extract_pdf_layout_preview_with_textract(
    preview_pdf_bytes: bytes,
) -> tuple[tuple[str, ...], tuple[tuple[ExtractedLine, ...], ...]]:
    """Read only the already bounded PDF preview and retain Textract line boxes."""
    result = TextractGateway(mode="text").analyze_pdf(raw_bytes=preview_pdf_bytes)
    extraction = map_textract_blocks_to_extraction(
        document_hash=str(result.get("document_hash") or ""),
        blocks=result.get("blocks") or [],
        page_count=int(result.get("page_count") or 0),
    )
    layout_lines = tuple(tuple(page.lines) for page in extraction.pages)
    page_texts = tuple("\n".join(line.text for line in lines) for lines in layout_lines)
    return page_texts, layout_lines
