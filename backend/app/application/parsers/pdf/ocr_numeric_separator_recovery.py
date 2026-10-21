"""Retry parsing existing OCR evidence after exact separator repair; no OCR/network requests."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace

from app.application.document_extraction_models import RawDocumentExtraction
from app.application.errors import InvalidFileContentError
from app.application.normalization.balance import annotate_balance_consistency, resolve_descending_running_balance
from app.application.normalization.ocr_numeric_separators import (
    _TOKEN,
    _contains,
    normalize_ocr_extraction,
    normalize_ocr_numeric_separators,
)
from app.application.normalization.pdf_amount_tokens import is_amount_like, parse_pdf_amount


def parse_with_ocr_separator_recovery(source, parse):
    if not isinstance(source, (RawDocumentExtraction, list, tuple)):
        return parse(source)
    normalized = (normalize_ocr_extraction(source) if isinstance(source, RawDocumentExtraction)
                  else [normalize_ocr_numeric_separators(page) for page in source])
    if normalized == source:
        return parse(source)
    baseline = baseline_error = None
    try:
        baseline = parse(source)
    except InvalidFileContentError as exc:
        baseline_error = exc
    try:
        candidate = parse(normalized)
    except InvalidFileContentError:
        if baseline is not None:
            return baseline
        raise baseline_error
    if _financially_confirmed(candidate, baseline, source):
        return _restore_evidence(candidate, source)
    if baseline is not None:
        return baseline
    raise baseline_error


def _financially_confirmed(candidate, baseline, source):
    metrics = candidate.parse_metrics
    if metrics.get("balance_consistency_failed", 0) or metrics.get("ocr_unresolved_rows", 0):
        return False
    rows = candidate.canonical_transactions or []
    if not rows:
        return False
    previous = Counter(_signature(row) for row in (baseline.canonical_transactions or [])) if baseline is not None else Counter()
    changed = set()
    values = _repaired_values(source)
    for index, row in enumerate(rows):
        signature = _signature(row)
        if previous[signature]:
            previous[signature] -= 1
        elif baseline is not None or abs(row.amount) in values or (row.running_balance is not None and abs(row.running_balance) in values):
            changed.add(index)
    if not changed:
        return False
    # A separator repair must not erase unrelated transactions already read.
    keys = {_identity(row) for row in rows}
    if baseline is not None and any(_identity(row) not in keys for row in baseline.canonical_transactions or []):
        return False
    if metrics.get("financial_controls_validated"):
        return True
    anchors = [index for index, row in enumerate(rows) if row.running_balance is not None and _printed_balance(row, source)]
    covered = set()
    descending = resolve_descending_running_balance(rows)
    for start, end in zip(anchors, anchors[1:]):
        checked, failed = annotate_balance_consistency(deepcopy(rows[start:end + 1]))
        if checked and not failed:
            covered.update(range(start, end) if descending else range(start + 1, end + 1))
    return changed <= covered


def _identity(row):
    return row.source_page, row.source_line, row.date


def _signature(row):
    return *_identity(row), row.amount, row.running_balance


def _texts(source):
    if not isinstance(source, RawDocumentExtraction):
        return source
    return [item.text for page in source.pages for item in [*page.lines, *page.words,
            *(cell for table in page.tables for row in table.rows for cell in row)]]


def _repaired_values(source):
    return {abs(parse_pdf_amount(normalize_ocr_numeric_separators(match[0]))) for text in _texts(source)
            for match in _TOKEN.finditer(text) if normalize_ocr_numeric_separators(match[0]) != match[0]}


def _printed_balance(row, source):
    if not isinstance(source, RawDocumentExtraction):
        return True
    page = next((page for page in source.pages if page.page_number == row.source_page), None)
    if page is None:
        return False
    if not page.words:
        return True
    matches = []
    for word in page.words:
        raw = normalize_ocr_numeric_separators(word.text)
        if not is_amount_like(raw) or parse_pdf_amount(raw) != row.running_balance:
            continue
        if any(region.page == row.source_page and _contains(vars(region), word.bbox) for region in row.source_regions):
            matches.append(word)
    return bool(matches) and all(word.text_type == "PRINTED" for word in matches)


def _restore_evidence(result, source):
    if isinstance(source, RawDocumentExtraction):
        by_number = {page.page_number: page for page in source.pages}
        texts = tuple("\n".join(line.text for line in sorted(by_number[number].lines, key=lambda line: line.line_index))
                      if number in by_number else "" for number in range(1, max(by_number, default=0) + 1))
        return replace(result, source_extraction=source, source_layout_lines=tuple(tuple(page.lines) for page in source.pages),
                       source_page_texts=texts, extracted_text="\n".join(texts))
    return replace(result, source_page_texts=tuple(source), extracted_text="\n".join(source))
