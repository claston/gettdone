"""Native glyph evidence in column fragments, preserving real PDF coordinates."""
import hashlib
from contextlib import closing
from statistics import median

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import fail


def read_native_table_source(raw):
    import pypdfium2 as pdfium

    pages = []
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for page_index in range(len(document)):
            with closing(document[page_index]) as page, closing(page.get_textpage()) as tp:
                if page.get_rotation():
                    fail("A orientação deste extrato nativo não permite conferir suas colunas.")
                width, height = page.get_size()
                chars = []
                for index in range(tp.count_chars()):
                    char = chr(pdfium.raw.FPDFText_GetUnicode(tp, index))
                    if char.isspace() or char in {"\x00", "\xad"}:
                        continue
                    left, bottom, right, top = tp.get_charbox(index, loose=True)
                    if right > left and top > bottom:
                        chars.append((left, bottom, right, top, char))
                if not chars:
                    fail("Uma página do extrato não contém texto nativo legível.")
                char_width = median(c[2] - c[0] for c in chars)
                tolerance = median(c[3] - c[1] for c in chars) * .35
                groups = []
                for cell in sorted(chars, key=lambda c: (-(c[1] + c[3]) / 2, c[0])):
                    y = (cell[1] + cell[3]) / 2
                    group = next((group for group in groups if abs(group[0] - y) < tolerance), None)
                    if group is None:
                        groups.append((y, [cell]))
                    else:
                        group[1].append(cell)
                lines, words = [], []

                def bbox(cells):
                    left, bottom = min(c[0] for c in cells), min(c[1] for c in cells)
                    right, top = max(c[2] for c in cells), max(c[3] for c in cells)
                    return {"left": left / width, "top": 1 - top / height,
                            "width": (right - left) / width, "height": (top - bottom) / height}

                def add_fragment(cells):
                    tokens = []
                    for cell in cells:
                        if not tokens or cell[0] - tokens[-1][-1][2] > char_width * .25:
                            tokens.append([cell])
                        else:
                            tokens[-1].append(cell)
                    word_ids = []
                    for token in tokens:
                        ident = f"native_p{page_index + 1}_w{len(words) + 1}"
                        word_ids.append(ident)
                        words.append(ExtractedWord(ident, page_index + 1, len(words) + 1,
                            "".join(c[4] for c in token), bbox=bbox(token), text_type="PRINTED"))
                    lines.append(ExtractedLine(f"native_p{page_index + 1}_l{len(lines) + 1}", page_index + 1,
                        len(lines) + 1, " ".join("".join(c[4] for c in token) for token in tokens),
                        bbox=bbox(cells), word_ids=tuple(word_ids)))

                for _, group in groups:
                    fragment = []
                    for cell in sorted(group, key=lambda c: c[0]):
                        if fragment and cell[0] - fragment[-1][2] > max(8, char_width * 2.2):
                            add_fragment(fragment)
                            fragment = []
                        fragment.append(cell)
                    if fragment:
                        add_fragment(fragment)
                pages.append(ExtractedPage(page_index + 1, lines=lines, words=words))
    return RawDocumentExtraction("native_pdfium_text", hashlib.sha256(raw).hexdigest(), pages, {"native_coordinates_used": 1})
