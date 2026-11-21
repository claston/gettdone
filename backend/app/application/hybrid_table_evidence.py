"""Detect painted financial cells missing from an otherwise readable PDF table."""
import re
from contextlib import closing

from app.application.normalization.text import normalize_upper_text as upper


def _hits(textpage, term):
    with closing(textpage.search(term, match_case=False)) as search:
        while (hit := search.get_next()) is not None:
            boxes = [textpage.get_charbox(i) for i in range(hit[0], hit[0] + hit[1])]
            yield (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))


def has_rasterized_financial_cells(raw):
    import pypdfium2 as pdfium

    from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK

    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        previous_column = None
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as tp:
                width, height = page.get_size()
                column, header_bottom = previous_column, height
                for left, bottom, right, top in _hits(tp, "Valor"):
                    band = upper(tp.get_text_bounded(0, bottom - 3, width, top + 3))
                    if (re.search(r"\bDATA\b", band)
                            and re.search(r"\b(?:DESCRICAO|HISTORICO|LANCAMENTO)\b", band)):
                        column, header_bottom = max(0, left / width - .02), bottom
                        previous_column = column
                        break
                if column is None:
                    continue
                for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                    bounds = image.get_bounds()
                    container = image.container
                    while container is not None:
                        bounds = container.get_matrix().on_rect(*bounds)
                        container = container.container
                    left, bottom, right, top = bounds
                    bottom, top = max(0, bottom), min(height, top)
                    if right <= column * width or left >= width or top >= header_bottom or top <= bottom:
                        continue
                    # A row background can contain the raster value as well as
                    # selectable history. Inspect the financial region itself.
                    cash = tp.get_text_bounded(column * width, bottom, width, top)
                    if re.search(r"\d[.,]\d{2}", cash):
                        continue
                    context = upper(tp.get_text_bounded(0, bottom - 2, column * width, top + 2))
                    if re.search(r"\b\d{2}/\d{2}/\d{4}\b|\b\d{6,}\b|\bSALDO DO DIA\b", context):
                        return True
    return False
