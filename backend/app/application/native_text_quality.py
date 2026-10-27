"""Evaluate embedded OCR text separately from the legibility of the page image."""

import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject

from app.application.errors import OcrFinancialAmbiguityError


@dataclass(frozen=True)
class NativeTextQuality:
    requires_visual_extraction: bool = False
    evidence: tuple[str, ...] = ()

    def metrics(self):
        if not self.requires_visual_extraction:
            return {}
        reason = ("image_pages_without_usable_text" if "sparse_native_text_on_image_pages" in self.evidence else
                  "vector_pages_without_usable_text" if "sparse_native_text_on_vector_pages" in self.evidence else
                  "unmapped_font_glyphs" if "unmapped_font_glyphs" in self.evidence else "embedded_ocr_text_unreliable")
        return {"native_text_quality_requires_visual": 1, "native_text_quality_reason": reason}


def _text_signals(text):
    malformed = 0
    for line in text.splitlines():
        if re.match(r"^\s*[©Oo�]\d/\d{2}/\d{4}\b", line):
            malformed += 1
        elif match := re.match(r"^\s*(\d{2}/\d{2}/\d{4})\b", line):
            try:
                datetime.strptime(match[1], "%d/%m/%Y")
            except ValueError:
                malformed += 1
    fragmented = len(re.findall(r"(?<!\d)\d[\d.]*,\s+\d{2}\b", text))
    signals = []
    if malformed:
        signals.append("malformed_date_tokens")
    if fragmented >= 3:
        signals.append("fragmented_money_values")
    return tuple(signals)


def _dereference(value):
    return value.get_object() if hasattr(value, "get_object") else value


def has_raster_with_invisible_text(page, reader):
    """Require a painted page-sized image and text drawn without a visible fill."""
    image, invisible, visible = _raster_and_text_visibility(page, reader)
    return image and invisible > 0 and visible == 0


def _raster_and_text_visibility(page, reader):
    contents = page.get_contents()
    if contents is None:
        return False, 0, 0
    resources = _dereference(page.get("/Resources", {}))
    xobjects = _dereference(resources.get("/XObject", {}))
    page_area = float(page.mediabox.width) * float(page.mediabox.height)
    area_scale, rendering_mode = 1.0, 0
    stack = []
    image = False
    invisible = visible = 0
    for operands, operator in ContentStream(contents, reader).operations:
        if operator == b"q":
            stack.append((area_scale, rendering_mode))
        elif operator == b"Q" and stack:
            area_scale, rendering_mode = stack.pop()
        elif operator == b"cm":
            a, b, c, d, _, _ = map(float, operands)
            area_scale *= abs(a * d - b * c)
        elif operator == b"Tr":
            rendering_mode = int(operands[0])
        elif operator in {b"Tj", b"TJ", b"'", b'"'}:
            invisible += rendering_mode in {3, 7}
            visible += rendering_mode not in {3, 7}
        elif operator == b"Do" and operands:
            obj = _dereference(xobjects.get(operands[0], {}))
            if obj.get("/Subtype") == "/Image" and area_scale >= page_area * 0.65:
                image = True
        elif operator == b"INLINE IMAGE" and area_scale >= page_area * 0.65:
            image = True
    return image, invisible, visible


def _has_large_raster_resource(resources, page_area, visited=None):
    resources = _dereference(resources)
    visited = visited if visited is not None else set()
    if not resources or id(resources) in visited:
        return False
    visited.add(id(resources))
    for value in _dereference(resources.get("/XObject", {})).values():
        obj = _dereference(value)
        if obj.get("/Subtype") == "/Image":
            width, height = float(obj.get("/Width", 0)), float(obj.get("/Height", 0))
            if min(width, height) >= 200 and width * height >= page_area * .5:
                return True
        elif obj.get("/Subtype") == "/Form" and _has_large_raster_resource(obj.get("/Resources", {}), page_area, visited):
            return True
    return False


def has_dense_vector_paths(page, reader):
    contents = page.get_contents()
    if contents is None:
        return False
    construction = paint = 0
    for _, operator in ContentStream(contents, reader).operations:
        construction += operator in {b"m", b"l", b"c", b"v", b"y", b"re", b"h"}
        paint += operator in {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
    return construction >= 360 and paint >= 100


def assess_native_text_quality(raw_bytes, page_texts, *, reader=None):
    # Type3 fonts without a Unicode map can yield thousands of placeholder
    # squares. Character count alone must not suppress visual extraction.
    for text in page_texts:
        unmapped = sum(text.count(character) for character in ("\u25a1", "\ufffd", "\x00"))
        glyph_names = re.findall(r"(?<!\S)/(?:i?\d+)\b", text)
        if (unmapped >= 30 and unmapped / max(1, len(text)) >= .12
                or len(glyph_names) >= 30 and sum(map(len, glyph_names)) / max(1, len(text)) >= .25):
            return NativeTextQuality(True, ("embedded_text_quality_low", "unmapped_font_glyphs"))
    try:
        reader = reader or PdfReader(BytesIO(raw_bytes))
        # A few native header characters must not hide scanned ledger pages.
        # Inspect every page: a scan can occur between otherwise native pages.
        # Dense vector exports need OCR too when only a header remains as text.
        for index, page in enumerate(reader.pages):
            area = float(page.mediabox.width * page.mediabox.height)
            contents = page.get_contents()
            data = contents.get_data() if contents is not None else b""
            inline_image = re.search(rb"(?:^|\s)BI(?:\s|$)", data) is not None
            raster = _has_large_raster_resource(page.get("/Resources", {}), area) or inline_image
            # Cheap candidate detection avoids re-extracting every page of long
            # ordinary native statements. Actual operators still validate paths.
            vector = len(re.findall(rb"(?<!\S)(?:S|s|f\*?|F|B\*?|b\*?)(?!\S)", data)) >= 100
            if not raster and not vector:
                continue
            text = page_texts[index] if len(page_texts) == len(reader.pages) else page.extract_text() or ""
            if len(text.strip()) >= 80:
                continue
            if vector and has_dense_vector_paths(page, reader):
                return NativeTextQuality(True, ("dense_vector_paths", "sparse_native_text_on_vector_pages"))
            if raster and _raster_and_text_visibility(page, reader)[0]:
                return NativeTextQuality(True, ("full_page_raster_present", "sparse_native_text_on_image_pages"))
        if not _text_signals("\n".join(page_texts)):
            return NativeTextQuality()
        evidence = []
        for page in reader.pages:
            signals = _text_signals(page.extract_text() or "")
            if signals and has_raster_with_invisible_text(page, reader):
                evidence.extend(signals)
        if evidence:
            return NativeTextQuality(
                True,
                tuple(dict.fromkeys(("full_page_raster_present", "invisible_text_layer_present", "embedded_text_quality_low", *evidence))),
            )
    except Exception:
        # Structural/read errors keep the existing PDF error handling path.
        return NativeTextQuality()
    return NativeTextQuality()


def prepare_visual_ocr_pdf(raw_bytes):
    """Preserve page images/coordinates while excluding the unreliable OCR layer."""
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            hidden = has_raster_with_invisible_text(page, reader)
            copied = writer.add_page(page)
            if hidden:
                stream = ContentStream(copied.get_contents(), writer)
                stream.operations = [
                    (operands, operator) for operands, operator in stream.operations if operator not in {b"Tj", b"TJ", b"'", b'"'}
                ]
                copied[NameObject("/Contents")] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception as exc:
        raise OcrFinancialAmbiguityError("Não foi possível preparar a imagem deste PDF para leitura. Reenvie o extrato original.") from exc
