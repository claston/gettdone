"""Bind original extraction evidence to transactions, without financial matching."""
import logging
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import replace

from app.application.parsers.pdf.models import PdfParseResult
from app.application.source_regions import SourceRegion, evidence_regions, parse_source_regions

logger = logging.getLogger(__name__)


def bind_transaction_source_regions(result: PdfParseResult, raw_bytes: bytes) -> PdfParseResult:
    rows = result.canonical_transactions
    if not rows:
        return result
    line_regions: dict[tuple[int, int], list[SourceRegion]] = {}
    if result.source_extraction is not None:
        for page in result.source_extraction.pages:
            if not result.source_page_texts or page.page_number > len(result.source_page_texts):
                continue
            source_lines = result.source_page_texts[page.page_number - 1].splitlines()
            extracted = [line for line in sorted(page.lines, key=lambda line: line.line_index) if line.text]
            # source_line indexes the parser's actual text stream, never a pixel
            # position or an OCR table row number.
            if [_text(line) for line in source_lines] != [_text(line.text) for line in extracted]:
                continue
            for number, line in enumerate(extracted, 1):
                line_regions[page.page_number, number] = evidence_regions(page.page_number, [line], words=page.words)
    elif result.source_page_texts and raw_bytes.lstrip().startswith(b"%PDF-"):
        try:
            line_regions = _native_line_regions(raw_bytes, result.source_page_texts or ())
        except Exception:
            # Optional navigation evidence cannot break a successful conversion.
            logger.debug("native_source_regions_unavailable", exc_info=True)
    enriched = []
    for row in rows:
        regions = parse_source_regions(row.source_regions)
        if not regions and not str(row.source_parser or "").startswith("textract_table"):
            references = row.source_line_refs or ((row.source_page, row.source_line),)
            regions = list(dict.fromkeys(region for ref in references for region in line_regions.get(ref, [])))
        enriched.append(replace(row, source_regions=regions))
    return replace(result, canonical_transactions=enriched)


def _text(value: str) -> str:
    return " ".join(value.split())


def _native_line_regions(raw_bytes: bytes, source_pages: tuple[str, ...]) -> dict[tuple[int, int], list[SourceRegion]]:
    import pypdfium2 as pdfium

    from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK

    result = {}
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw_bytes) as document:
        # Some native parsers omit empty pages. Never shift physical provenance.
        if len(document) != len(source_pages):
            return result
        for page_index, source in enumerate(source_pages):
            if not source.strip():
                continue
            with closing(document[page_index]) as page, closing(page.get_textpage()) as textpage:
                # Raw Unicode preserves indices used by get_charbox, unlike
                # get_text_range, which can insert or exclude characters.
                characters = "".join(chr(pdfium.raw.FPDFText_GetUnicode(textpage, index))
                                     for index in range(textpage.count_chars()))
                offset = 0
                lines = []
                fragments = []
                for raw_line in characters.splitlines(keepends=True):
                    text = _text(raw_line)
                    if text:
                        indices = [offset + index for index, char in enumerate(raw_line) if not char.isspace()]
                        rects = [textpage.get_charbox(index) for index in indices]
                        rect = (min(r[0] for r in rects), min(r[1] for r in rects),
                                max(r[2] for r in rects), max(r[3] for r in rects))
                        region = _visible_region(page_index + 1, rect, page.get_bbox(), page.get_rotation())
                        lines.append((text, region))
                        fragments.append((text, rect))
                    offset += len(raw_line)
                original = [(index, _text(line)) for index, line in enumerate(source.splitlines(), 1) if _text(line)]
                _bind_exact_lines(result, page_index + 1, original, lines)
                # Layout extraction can join columns which PDFium emits as
                # separate text objects. Reassemble only geometrically aligned
                # fragments, then require the same exact original line text.
                spatial = _spatial_lines(fragments, page_index + 1, page.get_bbox(), page.get_rotation())
                _bind_exact_lines(result, page_index + 1, original, spatial)
    return result


def _bind_exact_lines(result, page, original, lines):
    if [line for _, line in original] == [line for line, _ in lines]:
        # Equality of the entire stream disambiguates repeated rows.
        for (number, _), (_, region) in zip(original, lines, strict=True):
            result.setdefault((page, number), [region] if region else [])
    else:
        # Engines can differ in ordering/grouping. Only exact and
        # unique original lines are admissible; no fuzzy matching.
        counts = Counter(line for _, line in original)
        candidates = defaultdict(list)
        for line, region in lines:
            candidates[line].append(region)
        for number, line in original:
            matches = candidates[line]
            if counts[line] == 1 and len(matches) == 1 and matches[0]:
                result.setdefault((page, number), matches)


def _spatial_lines(fragments, page, crop, rotation):
    groups = []
    for text, rect in sorted(fragments, key=lambda item: (-item[1][3], item[1][0])):
        left, bottom, right, top = rect
        for group in reversed(groups):
            _, gb, _, gt = group[0][1]
            overlap = min(top, gt) - max(bottom, gb)
            if overlap >= min(top-bottom, gt-gb) * .7 and overlap > 0:
                # Overlapping objects may be duplicate/hidden text. Do not
                # collapse them into a newly invented line association.
                if all(right <= other[1][0] or left >= other[1][2] for other in group):
                    group.append((text, rect))
                    break
        else:
            groups.append([(text, rect)])
    lines = []
    for group in groups:
        ordered = sorted(group, key=lambda item: item[1][0])
        rect = (min(item[1][0] for item in group), min(item[1][1] for item in group),
                max(item[1][2] for item in group), max(item[1][3] for item in group))
        lines.append((_text(" ".join(item[0] for item in ordered)), _visible_region(page, rect, crop, rotation)))
    return lines


def _visible_region(page, rect, crop, rotation):
    x0, y0, x1, y1 = crop
    if x1 <= x0 or y1 <= y0:
        return None
    left, bottom, right, top = rect
    points = []
    for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
        u, v = (x - x0) / (x1 - x0), (y1 - y) / (y1 - y0)
        points.append({0: (u, v), 90: (1 - v, u), 180: (1 - u, 1 - v), 270: (v, 1 - u)}[rotation])
    left, top = max(0, min(p[0] for p in points)), max(0, min(p[1] for p in points))
    right, bottom = min(1, max(p[0] for p in points)), min(1, max(p[1] for p in points))
    regions = parse_source_regions([{"page": page, "left": left, "top": top, "width": right - left, "height": bottom - top}])
    return regions[0] if regions else None
