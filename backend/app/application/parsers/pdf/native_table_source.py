"""Native glyph evidence in column fragments, preserving real PDF coordinates."""
import hashlib
from contextlib import closing
from statistics import median

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import fail


def native_header_columns(page, keys):
    """A merged LINE may still contain independently positioned header WORDs."""
    from app.application.normalization.text import normalize_upper_text as upper
    from app.application.parsers.pdf.followup_ocr import _headers

    columns = _headers(page, keys)
    if columns:
        return columns
    candidates = []
    for anchor in page.words:
        if not anchor.bbox or upper(anchor.text) != keys[0].split()[0]:
            continue
        row = sorted([word for word in page.words if word.bbox
            and abs(word.bbox["top"]-anchor.bbox["top"]) < max(.002, anchor.bbox["height"]*.4)],
            key=lambda word: word.bbox["left"])
        found = {}
        for key in keys:
            choices = [words[0].bbox["left"] for start in range(len(row))
                for words in [row[start:start+len(key.split())]]
                if upper(" ".join(word.text for word in words)) == key]
            if len(choices) != 1:
                break
            found[key] = choices[0]
        if len(found) == len(keys) and list(found.values()) == sorted(found.values()):
            candidates.append(found)
    return candidates[0] if len(candidates) == 1 else None


def split_native_columns(page, boundaries, *, include=None):
    """Separate only real WORD evidence crossing known printed column boundaries."""
    from dataclasses import replace

    words = {word.id: word for word in page.words}
    lines = []
    for line in page.lines:
        if include is not None and not include(line):
            lines.append(line)
            continue
        groups = {}
        for identifier in line.word_ids:
            word = words.get(identifier)
            if word is not None and word.bbox:
                column = sum(word.bbox["left"] >= boundary for boundary in boundaries)
                groups.setdefault(column, []).append(word)
        if len(groups) < 2:
            lines.append(line)
            continue
        for column, group in sorted(groups.items()):
            group.sort(key=lambda word: word.bbox["left"])
            left, top = min(word.bbox["left"] for word in group), min(word.bbox["top"] for word in group)
            right = max(word.bbox["left"] + word.bbox["width"] for word in group)
            bottom = max(word.bbox["top"] + word.bbox["height"] for word in group)
            lines.append(replace(line, id=f"{line.id}:column{column}", text=" ".join(word.text for word in group),
                bbox={"left": left, "top": top, "width": right-left, "height": bottom-top},
                word_ids=tuple(word.id for word in group)))
    return replace(page, lines=lines)


def read_native_table_source(raw, *, decorative_history=None):
    import pypdfium2 as pdfium

    pages, excluded, empty = [], 0, 0
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
                    if decorative_history:
                        start, end, max_height = decorative_history
                        if start <= left / width < right / width <= end and (top - bottom) / height > max_height:
                            if not (char.isalpha() or char == "-"):
                                fail("Uma marca sobreposta interfere nos dados da tabela.")
                            excluded += 1
                            continue
                    if right > left and top > bottom:
                        chars.append((left, bottom, right, top, char))
                if not chars:
                    # Only an object-free, visibly white page can be ignored.
                    # Keep its page number: source references must not shift.
                    if not list(page.get_objects()) and page.render(scale=.5).to_pil().convert("L").getextrema() == (255, 255):
                        pages.append(ExtractedPage(page_index + 1, lines=[], words=[]))
                        empty += 1
                        continue
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
    metrics = {"native_coordinates_used": 1}
    if empty:
        metrics["empty_native_pages_verified"] = empty
    if excluded:
        metrics["decorative_history_glyphs_excluded"] = excluded
    return RawDocumentExtraction("native_pdfium_text", hashlib.sha256(raw).hexdigest(), pages, metrics)
