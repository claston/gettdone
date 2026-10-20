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
        reason = "unmapped_font_glyphs" if "unmapped_font_glyphs" in self.evidence else "embedded_ocr_text_unreliable"
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
    contents = page.get_contents()
    if contents is None:
        return False
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
    return image and invisible > 0 and visible == 0


def assess_native_text_quality(raw_bytes, page_texts, *, reader=None):
    # Type3 fonts without a Unicode map can yield thousands of placeholder
    # squares. Character count alone must not suppress visual extraction.
    for text in page_texts:
        unmapped = sum(text.count(character) for character in ("\u25a1", "\ufffd"))
        if unmapped >= 30 and unmapped / max(1, len(text)) >= .12:
            return NativeTextQuality(True, ("embedded_text_quality_low", "unmapped_font_glyphs"))
    if not _text_signals("\n".join(page_texts)):
        return NativeTextQuality()
    try:
        reader = reader or PdfReader(BytesIO(raw_bytes))
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
