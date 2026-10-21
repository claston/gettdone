"""Conservative monetary separator repairs in an OCR parsing view, never in source evidence."""

import re
from dataclasses import replace

from app.application.document_extraction_models import RawDocumentExtraction

_TOKEN = re.compile(r"(?<![\w.,/+\-])([+-]?)(\d{1,3}(?:,\d{3})+),(\d{2})([+\-CDcd]?)(?![\w.,/+\-])")


def normalize_ocr_numeric_separators(text: str, *, allowed: set[str] | None = None) -> str:
    def repair(match):
        if (match[1] and match[4]) or (allowed is not None and match[0] not in allowed):
            return match[0]
        return f"{match[1]}{match[2].replace(',', '.')},{match[3]}{match[4]}"

    return _TOKEN.sub(repair, text)


def normalize_ocr_extraction(source: RawDocumentExtraction) -> RawDocumentExtraction:
    pages = []
    for page in source.pages:
        words_by_id = {word.id: word for word in page.words}

        def container(item):
            allowed = None
            if page.words:
                children = [words_by_id[key] for key in item.word_ids if key in words_by_id]
                if not item.word_ids and item.bbox:
                    children = [word for word in page.words if _contains(item.bbox, word.bbox)]
                protected = {match[0] for word in children if word.text_type != "PRINTED" for match in _TOKEN.finditer(word.text)}
                allowed = {match[0] for word in children if word.text_type == "PRINTED" for match in _TOKEN.finditer(word.text)} - protected
            return replace(item, text=normalize_ocr_numeric_separators(item.text, allowed=allowed))

        pages.append(replace(page,
            words=[replace(word, text=normalize_ocr_numeric_separators(word.text)) if word.text_type == "PRINTED" else word
                   for word in page.words],
            lines=[container(line) for line in page.lines],
            tables=[replace(table, rows=[[container(cell) for cell in row] for row in table.rows]) for table in page.tables],
        ))
    normalized = replace(source, pages=pages)
    return source if normalized == source else normalized


def _contains(outer, inner) -> bool:
    if not outer or not inner:
        return False
    x, y = inner["left"] + inner["width"] / 2, inner["top"] + inner["height"] / 2
    return (outer["left"] - .001 <= x <= outer["left"] + outer["width"] + .001
            and outer["top"] - .001 <= y <= outer["top"] + outer["height"] + .001)
