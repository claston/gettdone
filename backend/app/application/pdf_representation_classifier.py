from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.generic import ContentStream

CLASSIFIER_VERSION = "pdf_representation_v1"

_VIRTUAL_PRINTER_PRODUCERS = (
    ("microsoft print to pdf", "producer_microsoft_print_to_pdf"),
    ("pdfcreator", "producer_pdfcreator"),
    ("cutepdf", "producer_cutepdf"),
    ("dopdf", "producer_dopdf"),
    ("bullzip", "producer_bullzip"),
    ("foxit pdf printer", "producer_foxit_pdf_printer"),
)
_PATH_CONSTRUCTION_OPERATORS = {"m", "l", "c", "v", "y", "re", "h"}
_PATH_PAINT_OPERATORS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
_MIN_VECTOR_PATH_OPERATIONS = 360
_MIN_VECTOR_PAINT_OPERATIONS = 100


@dataclass(frozen=True, slots=True)
class PdfRepresentationClassification:
    representation: str
    creation_method: str
    confidence: float
    evidence: tuple[str, ...]
    classifier_version: str = CLASSIFIER_VERSION
    requires_visual_extraction: bool = False


def classify_pdf_representation(raw_bytes: bytes) -> PdfRepresentationClassification:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        producer_marker = _virtual_printer_producer_marker(reader)
        creation_method = "virtual_print" if producer_marker else "unknown"
        sampled_pages = _sample_pages(reader)
        has_fonts = _has_font_resources(reader)
        extracted_chars = _count_extractable_characters(reader) if has_fonts else 0
        dense_vector_paths = _has_dense_vector_paths(sampled_pages, reader)
        full_page_raster = _has_full_page_raster_image(sampled_pages)
    except Exception:
        return PdfRepresentationClassification(
            representation="unknown_no_text",
            creation_method="unknown",
            confidence=0.0,
            evidence=("pdf_structure_unreadable",),
        )

    if extracted_chars > 0:
        evidence = ["native_text_present"]
        if full_page_raster or dense_vector_paths:
            evidence.append("non_text_page_content_present")
            if producer_marker:
                evidence.append(producer_marker)
            return PdfRepresentationClassification(
                representation="mixed",
                creation_method=creation_method if producer_marker else "direct_export",
                confidence=0.9,
                evidence=tuple(evidence),
            )
        if producer_marker:
            evidence.append(producer_marker)
        return PdfRepresentationClassification(
            representation="native_text",
            creation_method=creation_method if producer_marker else "direct_export",
            confidence=0.99,
            evidence=tuple(evidence),
        )

    common_evidence = ["native_text_absent"]
    if not has_fonts:
        common_evidence.append("font_resources_absent")

    if full_page_raster:
        evidence = [*common_evidence, "full_page_raster_present"]
        if producer_marker:
            evidence.append(producer_marker)
        return PdfRepresentationClassification(
            representation="raster_images",
            creation_method=creation_method,
            confidence=0.97,
            evidence=tuple(evidence),
            requires_visual_extraction=True,
        )

    if dense_vector_paths:
        evidence = [*common_evidence, "dense_vector_paths", "full_page_raster_absent"]
        if producer_marker:
            evidence.append(producer_marker)
        return PdfRepresentationClassification(
            representation="vector_outlines",
            creation_method=creation_method,
            confidence=0.99 if producer_marker else 0.9,
            evidence=tuple(evidence),
            requires_visual_extraction=True,
        )

    if producer_marker:
        common_evidence.append(producer_marker)
    return PdfRepresentationClassification(
        representation="unknown_no_text",
        creation_method=creation_method,
        confidence=0.5 if producer_marker else 0.2,
        evidence=tuple(common_evidence),
    )


def _virtual_printer_producer_marker(reader: PdfReader) -> str | None:
    metadata = reader.metadata or {}
    producer = _normalize_metadata_value(metadata.get("/Producer"))
    creator = _normalize_metadata_value(metadata.get("/Creator"))
    combined = f"{producer} {creator}".strip()
    for marker, evidence in _VIRTUAL_PRINTER_PRODUCERS:
        if marker in combined:
            return evidence
    return None


def _normalize_metadata_value(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().casefold()).strip()


def _count_extractable_characters(reader: PdfReader) -> int:
    extracted_chars = 0
    for page in reader.pages:
        extracted_chars += len((page.extract_text() or "").strip())
        if extracted_chars >= 40:
            break
    return extracted_chars


def _sample_pages(reader: PdfReader) -> list[object]:
    page_count = len(reader.pages)
    if page_count <= 4:
        return list(reader.pages)
    indexes = sorted({0, page_count // 2, page_count - 1})
    return [reader.pages[index] for index in indexes]


def _has_font_resources(reader: PdfReader) -> bool:
    for page in reader.pages:
        resources = _dereference(page.get("/Resources", {}))
        fonts = _dereference(resources.get("/Font", {})) if resources else {}
        if fonts:
            return True
    return False


def _has_dense_vector_paths(pages: list[object], reader: PdfReader) -> bool:
    for page in pages:
        contents = page.get_contents()
        if contents is None:
            continue
        operations = ContentStream(contents, reader).operations
        construction_count = 0
        paint_count = 0
        for _, operator in operations:
            normalized = operator.decode("latin-1") if isinstance(operator, bytes) else str(operator)
            if normalized in _PATH_CONSTRUCTION_OPERATORS:
                construction_count += 1
            elif normalized in _PATH_PAINT_OPERATORS:
                paint_count += 1
        if (
            construction_count >= _MIN_VECTOR_PATH_OPERATIONS
            and paint_count >= _MIN_VECTOR_PAINT_OPERATIONS
        ):
            return True
    return False


def _has_full_page_raster_image(pages: list[object]) -> bool:
    if not pages:
        return False
    pages_with_dominant_image = 0
    for page in pages:
        page_width = max(1.0, float(page.mediabox.width))
        page_height = max(1.0, float(page.mediabox.height))
        page_aspect = page_width / page_height
        resources = _dereference(page.get("/Resources", {}))
        xobjects = _dereference(resources.get("/XObject", {})) if resources else {}
        dominant_image_found = False
        for xobject_ref in xobjects.values():
            xobject = _dereference(xobject_ref)
            if str(xobject.get("/Subtype")) != "/Image":
                continue
            width = float(xobject.get("/Width", 0) or 0)
            height = float(xobject.get("/Height", 0) or 0)
            if width <= 0 or height <= 0:
                continue
            image_aspect = width / height
            comparable_aspect = abs(image_aspect - page_aspect) / page_aspect <= 0.2
            enough_pixels = width * height >= page_width * page_height * 0.5
            if comparable_aspect and enough_pixels:
                dominant_image_found = True
                break
        if dominant_image_found:
            pages_with_dominant_image += 1
    return pages_with_dominant_image >= max(1, (len(pages) + 1) // 2)


def _dereference(value):
    return value.get_object() if hasattr(value, "get_object") else value
