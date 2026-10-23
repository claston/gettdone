"""Read horizontal native glyphs, excluding diagonal watermarks."""

from contextlib import closing
from math import pi
from statistics import median

from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import fail


def read_horizontal_native_pages(raw, *, word_gap_ratio=.5):
    import pypdfium2 as pdfium

    pages = []
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                if page.get_rotation():
                    fail()
                chars = []
                for n in range(textpage.count_chars()):
                    angle = pdfium.raw.FPDFText_GetCharAngle(textpage, n)
                    if min(abs(angle), abs(angle - 2*pi)) > .05:
                        continue
                    char = chr(pdfium.raw.FPDFText_GetUnicode(textpage, n))
                    if char.isspace() or char == "\x00":
                        continue
                    left, bottom, right, top = textpage.get_charbox(n, loose=True)
                    chars.append(((bottom+top)/2, left, right, top-bottom, char))
                if not chars:
                    fail()
                tolerance = median(c[3] for c in chars) * .4
                gap = median(c[2]-c[1] for c in chars) * word_gap_ratio
                groups = []
                for y, left, right, height, char in sorted(chars, key=lambda c: (-c[0], c[1])):
                    group = next((g for g in groups if abs(g[0]-y) < tolerance), None)
                    if group is None:
                        groups.append([y, [(left, right, char)]])
                    else:
                        group[1].append((left, right, char))
                lines = []
                for _, cells in groups:
                    value, previous = "", None
                    for left, right, char in sorted(cells):
                        if previous is not None and left-previous > gap:
                            value += " "
                        value += char
                        previous = right
                    lines.append(value)
                pages.append("\n".join(lines))
    return pages
