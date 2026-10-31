"""Experimental OCR structure reconstruction; no bank, SDK or production routing.

Provider cells and positioned words produce the same evidence-bearing row model.
This opt-in module deliberately does not participate in native-PDF parsing.
"""

import re
from dataclasses import dataclass
from itertools import islice, product
from statistics import median

from app.application.normalization.text import normalize_upper_text
from app.application.source_regions import evidence_regions


@dataclass(frozen=True)
class OcrColumn:
    key: str
    headers: tuple[str, ...]
    anchor_pattern: str | None = None


@dataclass(frozen=True)
class OcrTableSchema:
    columns: tuple[OcrColumn, ...]

    def __post_init__(self):
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)) or len(keys) < 2 or not self.columns[0].anchor_pattern:
            raise ValueError("Unique columns and a primary row-anchor pattern are required")


@dataclass(frozen=True)
class OcrIssue:
    code: str
    page_number: int
    row_number: int | None = None


@dataclass(frozen=True)
class OcrCell:
    text: str
    word_ids: tuple[str, ...]
    source_regions: tuple
    confidence: float | None
    confidence_scope: str


@dataclass(frozen=True)
class OcrRow:
    page_number: int
    row_number: int
    cells: dict[str, OcrCell]
    evidence_kind: str


@dataclass(frozen=True)
class OcrStructure:
    rows: tuple[OcrRow, ...]
    issues: tuple[OcrIssue, ...]
    expected_rows: int
    page_slopes: dict[int, float]


def normalized_label(text):
    return re.sub(r"[^A-Z0-9]+", " ", normalize_upper_text(text)).strip()


def projected_y(item, slope):
    box = item.bbox
    return box["top"] + box["height"] / 2 - slope * (box["left"] + box["width"] / 2)


def word_slope(page):
    slopes = []
    for word in page.words:
        polygon = word.polygon
        if polygon and len(polygon) >= 2:
            dx = polygon[1]["x"] - polygon[0]["x"]
            if dx > 0.01:
                slopes.append((polygon[1]["y"] - polygon[0]["y"]) / dx)
    return median(slopes) if slopes else 0.0


def _headers(page, schema):
    choices = []
    for column in schema.columns:
        labels = {normalized_label(label) for label in column.headers}
        matches = [item for item in [*page.words, *page.lines] if item.bbox and normalized_label(item.text) in labels]
        # A WORD and its parent LINE are two views of one printed header.
        unique = {(round(item.bbox["left"], 4), round(item.bbox["top"], 4)): item for item in matches}
        choices.append(list(unique.values()))
    if not all(choices):
        return None
    candidates = []
    for row in islice(product(*choices), 512):
        xs = [item.bbox["left"] + item.bbox["width"] / 2 for item in row]
        if xs != sorted(xs) or len(set(xs)) != len(xs):
            continue
        ys = [item.bbox["top"] + item.bbox["height"] / 2 for item in row]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
        residual = max(abs(y - mean_y - slope * (x - mean_x)) for x, y in zip(xs, ys))
        if residual <= 0.012 and abs(slope) < 0.3:
            candidates.append((residual, mean_y, row, slope))
    if not candidates:
        return None
    _, _, row, slope = min(candidates, key=lambda candidate: candidate[:2])
    return row, slope


def _cell(page, items, text=None):
    word_ids = tuple(
        dict.fromkeys(identifier for item in items for identifier in ((item.id,) if hasattr(item, "word_index") else item.word_ids))
    )
    confidences = [item.confidence for item in items if item.confidence is not None]
    return OcrCell(
        text if text is not None else " ".join(item.text for item in items),
        word_ids,
        tuple(evidence_regions(page.page_number, items, words=page.words)),
        min(confidences) / 100 if confidences else None,
        "word" if items and all(hasattr(item, "word_index") for item in items) else "cell",
    )


def _unassigned_anchors(page, schema, page_rows, words, boundaries, top, slope):
    issues = []
    for index, column in enumerate(schema.columns):
        if not column.anchor_pattern:
            continue
        consumed = {identifier for row in page_rows for identifier in row.cells[column.key].word_ids}
        for word in words:
            x = word.bbox["left"] + word.bbox["width"] / 2
            if (
                sum(x >= boundary for boundary in boundaries) == index
                and projected_y(word, slope) > top
                and re.fullmatch(column.anchor_pattern, word.text.strip())
                and word.id not in consumed
            ):
                issues.append(OcrIssue("unassigned_column_anchor", page.page_number))
                break
    return issues


def _reading_order(words, slope):
    if not words:
        return []
    tolerance = median(word.bbox["height"] for word in words) * 0.65
    lines = []
    for word in sorted(words, key=lambda word: projected_y(word, slope)):
        y = projected_y(word, slope)
        if not lines or y - lines[-1][0] > tolerance:
            lines.append((y, [word]))
        else:
            lines[-1][1].append(word)
    return [word for _, line in lines for word in sorted(line, key=lambda word: word.bbox["left"])]


def _table_rows(page, schema):
    rows = []
    recognized = False
    for table in page.tables:
        mapping = None
        for table_row in table.rows:
            found = {
                column.key: next(
                    (
                        index
                        for index, cell in enumerate(table_row)
                        if normalized_label(cell.text) in {normalized_label(h) for h in column.headers}
                    ),
                    None,
                )
                for column in schema.columns
            }
            if all(index is not None for index in found.values()):
                mapping, recognized = found, True
                continue
            if mapping is None or not any(cell.text.strip() for cell in table_row):
                continue
            cells = {key: _cell(page, [table_row[index]]) if index < len(table_row) else _cell(page, []) for key, index in mapping.items()}
            rows.append(OcrRow(page.page_number, len(rows) + 1, cells, "cells"))
    return rows, recognized


def _infer_three_columns(page, schema, slope):
    """Header-free evidence for a primary/middle/value shape, without fixed coordinates."""
    if len(schema.columns) != 3 or not schema.columns[-1].anchor_pattern:
        return None
    words = [word for word in page.words if word.bbox]
    primary = [word for word in words if re.fullmatch(schema.columns[0].anchor_pattern, word.text.strip())]
    values = [word for word in words if re.fullmatch(schema.columns[-1].anchor_pattern, word.text.strip())]
    if not primary or not values:
        return None
    leftmost = min(word.bbox["left"] + word.bbox["width"] / 2 for word in primary)
    primary = [word for word in primary if abs(word.bbox["left"] + word.bbox["width"] / 2 - leftmost) < 0.04]
    rightmost = max(word.bbox["left"] + word.bbox["width"] / 2 for word in values)
    values = [word for word in values if abs(word.bbox["left"] + word.bbox["width"] / 2 - rightmost) < 0.08]
    left_end = max(word.bbox["left"] + word.bbox["width"] for word in primary)
    right_start = min(word.bbox["left"] for word in values)
    if right_start - left_end < 0.1:
        return None
    ys = sorted(projected_y(word, slope) for word in primary)
    gap = median([b - a for a, b in zip(ys, ys[1:])]) if len(ys) > 1 else 0.04
    return [left_end + 0.02, right_start - 0.02], max(0.0, ys[0] - gap / 2)


def reconstruct_ocr_table(extraction, schema: OcrTableSchema) -> OcrStructure:
    rows, issues, slopes = [], [], {}
    expected = 0
    previous_layout = None
    for page in extraction.pages:
        headers = _headers(page, schema)
        slope = headers[1] if headers else word_slope(page)
        slopes[page.page_number] = slope
        table_rows, recognized = _table_rows(page, schema)
        if headers:
            header_items = headers[0]
            starts = [item.bbox["left"] for item in header_items]
            boundaries = [starts[index] - (starts[index] - starts[index - 1]) * 0.1 for index in range(1, len(starts))]
            top = max(projected_y(item, slope) for item in header_items) + 0.007
            previous_layout = boundaries
        elif previous_layout is not None:
            boundaries, top = previous_layout, 0.0
        else:
            inferred = _infer_three_columns(page, schema, slope)
            boundaries, top = inferred if inferred else (None, 0.0)
            if inferred:
                previous_layout = boundaries
        words = [word for word in page.words if word.bbox]
        if not words:
            issues.append(OcrIssue("geometry_unavailable", page.page_number))
        if boundaries is None and not recognized:
            issues.append(OcrIssue("header_unavailable", page.page_number))
            continue
        primary_header = next(
            (
                line
                for line in page.lines
                if line.bbox and normalized_label(line.text) in {normalized_label(h) for h in schema.columns[0].headers}
            ),
            None,
        )
        # Independent coverage also works when only the provider's cell header is readable.
        primary_right = (
            boundaries[0]
            if boundaries
            else min(
                (
                    row.cells[schema.columns[0].key].source_regions[0].left
                    + row.cells[schema.columns[0].key].source_regions[0].width
                    + 0.02
                    for row in table_rows
                    if row.cells[schema.columns[0].key].source_regions
                ),
                default=0.2,
            )
        )
        anchor_top = top if headers or not primary_header else projected_y(primary_header, slope) + 0.007
        anchors = sorted(
            [
                word
                for word in words
                if word.bbox["left"] + word.bbox["width"] / 2 < primary_right
                and projected_y(word, slope) > anchor_top
                and re.fullmatch(schema.columns[0].anchor_pattern, word.text.strip())
            ],
            key=lambda word: projected_y(word, slope),
        )
        expected += len(anchors)
        if recognized:
            rows.extend(table_rows)
            consumed = {identifier for row in table_rows for identifier in row.cells[schema.columns[0].key].word_ids}
            if len(table_rows) != len(anchors) or any(anchor.id not in consumed for anchor in anchors):
                issues.append(OcrIssue("row_coverage_mismatch", page.page_number))
            if boundaries is not None:
                issues.extend(_unassigned_anchors(page, schema, table_rows, words, boundaries, top, slope))
            continue
        if not anchors:
            issues.append(OcrIssue("no_row_anchors", page.page_number))
            continue
        ys = [projected_y(anchor, slope) for anchor in anchors]
        default_gap = median([b - a for a, b in zip(ys, ys[1:])]) if len(ys) > 1 else 0.04
        page_rows = []
        for index, anchor in enumerate(anchors):
            upper = (ys[index - 1] + ys[index]) / 2 if index else max(top, ys[index] - default_gap / 2)
            lower = (ys[index] + ys[index + 1]) / 2 if index + 1 < len(ys) else ys[index] + default_gap / 2
            groups = {column.key: [] for column in schema.columns}
            for word in words:
                if upper <= projected_y(word, slope) < lower:
                    x = word.bbox["left"] + word.bbox["width"] / 2
                    column = sum(x >= boundary for boundary in boundaries)
                    groups[schema.columns[column].key].append(word)
            cells = {key: _cell(page, _reading_order(group, slope)) for key, group in groups.items()}
            if len(groups[schema.columns[0].key]) != 1:
                issues.append(OcrIssue("ambiguous_row_anchor", page.page_number, index + 1))
            page_rows.append(OcrRow(page.page_number, index + 1, cells, "words"))
        rows.extend(page_rows)
        issues.extend(_unassigned_anchors(page, schema, page_rows, words, boundaries, top, slope))
    return OcrStructure(tuple(rows), tuple(issues), expected, slopes)
