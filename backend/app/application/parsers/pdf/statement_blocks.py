"""Parse repeated statement scopes independently without losing physical provenance."""

import calendar
import re
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

from app.application.document_type_classifier import BANK_STATEMENT, classify_document_type
from app.application.errors import PdfFinancialValidationError
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.models import PdfParseResult
from app.application.pdf_document_segmentation import _statement_header, segment_pdf_pages
from app.application.statement_period_history import statement_period_from_text

_DATE = r"\d{2}/\d{2}/\d{4}"
_MONTHS = "JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ".split()
_FULL_MONTHS = "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split()
_PAGE_FIELDS = {"page", "page_number", "source_page", "reference_page"}


@dataclass(frozen=True)
class StatementBlock:
    start: int
    stop: int
    period: tuple[str, str] | None


def _period(text):
    header = _statement_header(text)
    period = statement_period_from_text(re.sub(r"PERIODO\s*:\s*DE\s+", "PERIODO: ", header))
    if period:
        return period["start_date"], period["end_date"]
    short = re.search(r"PERIODO:\s*(\d{2})\s+A\s+(\d{2})/(\d{2})/(\d{4})\b", header)
    try:
        labelled_month = re.search(r"\bPERIODO:\s*(" + "|".join(_FULL_MONTHS + _MONTHS) + r")/(20\d{2})\b", header)
        if labelled_month:
            names = _FULL_MONTHS if labelled_month[1] in _FULL_MONTHS else _MONTHS
            number, year = names.index(labelled_month[1])+1, int(labelled_month[2])
            return date(year, number, 1).isoformat(), date(year, number, calendar.monthrange(year, number)[1]).isoformat()
        if short:
            start, end, month, year = map(int, short.groups())
            return date(year, month, start).isoformat(), date(year, month, end).isoformat()
        for line in text.splitlines():
            month = re.fullmatch(r"(?:EXTRATO MENSAL\s+)?(" + "|".join(_MONTHS) + r")[ /]+(20\d{2})", upper(line))
            if month:
                number, year = _MONTHS.index(month[1]) + 1, int(month[2])
                return date(year, number, 1).isoformat(), date(year, number, calendar.monthrange(year, number)[1]).isoformat()
    except ValueError:
        # Invalid evidence belongs to the reader's validation, never an invented scope.
        return None
    return None


def statement_blocks(pages):
    if len(pages) < 2:
        return []
    from app.application.parsers.pdf.positional_monthly_statements import positional_kind

    blocks, start, current, opening, positional_month = [], 0, None, None, None
    for index, text in enumerate(pages):
        normalized = upper(text)
        match = re.search(rf"({_DATE})\s+SALDO\s+(?:ANTERIOR|INICIAL)\b", normalized)
        opening_date = match[1] if match else None
        first_dated = next((upper(line) for line in text.splitlines() if re.match(rf"^\s*{_DATE}\b", line)), "")
        opening_starts_ledger = bool(re.match(rf"{_DATE}\s+SALDO\s+(?:ANTERIOR|INICIAL)\b", first_dated))
        positional = positional_kind(text)
        is_statement = classify_document_type("page.pdf", b"", extracted_text=text).document_type == BANK_STATEMENT or bool(positional)
        period = _period(text) if is_statement else None
        lot_date = re.search(r"(?m)^\s*\d{6,8}\s+\d{3}#?\s+(\d{2}/\d{2}/(?:\d{4}|\d{2}))\b",
                             "\n".join(upper(line) for line in text.splitlines()))
        month_key = lot_date[1][3:] if positional == "lot_origin" and lot_date else None
        positional_reset = month_key and positional_month and month_key != positional_month
        changed = period is not None and current is not None and period != current
        dated_reset = (period is None and current is None and opening_date and opening
                       and opening_date != opening and opening_starts_ledger)
        if index and (changed or dated_reset or positional_reset):
            blocks.append(StatementBlock(start, index, current))
            start, opening = index, None
        if period:
            current = period
        if opening_date:
            opening = opening_date
        if month_key:
            positional_month = month_key
    if not blocks:
        return []
    blocks.append(StatementBlock(start, len(pages), current))
    # Owner identity and annex rules remain independent of document periods.
    segment_pdf_pages(pages)
    return blocks


def shift_pages(value, offset):
    if is_dataclass(value):
        return replace(value, **{field.name: getattr(value, field.name) + offset
            if field.name in _PAGE_FIELDS and isinstance(getattr(value, field.name), int)
            else shift_pages(getattr(value, field.name), offset) for field in fields(value)})
    if isinstance(value, dict):
        return {key: item + offset if key in _PAGE_FIELDS and isinstance(item, int)
                else shift_pages(item, offset) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(shift_pages(item, offset) for item in value)
    return value


def slice_extraction(extraction, block):
    return replace(extraction, pages=[shift_pages(page, -block.start) for page in extraction.pages[block.start:block.stop]])


def parse_native_blocks(raw, pages, parse):
    blocks = statement_blocks(pages)
    if not blocks:
        return None
    from pypdf import PdfWriter

    from app.application.parsers.pdf.reader import open_pdf_reader

    reader = open_pdf_reader(BytesIO(raw))
    results = []
    for block in blocks:
        writer = PdfWriter()
        for page in reader.pages[block.start:block.stop]:
            writer.add_page(page)
        if reader.metadata:
            writer.add_metadata({key: value for key, value in reader.metadata.items() if isinstance(value, str)})
        buffer = BytesIO()
        writer.write(buffer)
        results.append(parse(buffer.getvalue()))
    return combine_statement_results(results, blocks, pages, document_hash=sha256(raw).hexdigest())


def _bounds(result):
    metrics = result.parse_metrics
    if metrics.get("balance_bounds_unavailable") or metrics.get("opening_balance_ambiguous"):
        return None
    for prefix in ("verified", "printed", "native_cash"):
        if f"{prefix}_opening_balance" in metrics and f"{prefix}_closing_balance" in metrics:
            return Decimal(str(metrics[f"{prefix}_opening_balance"])), Decimal(str(metrics[f"{prefix}_closing_balance"]))
    return None


def combine_statement_results(results, blocks, pages, *, original_extraction=None, document_hash=""):
    if any(result.financial_document is not None for result in results):
        raise PdfFinancialValidationError("Os blocos têm representações financeiras diferentes e precisam de conferência de escopo.")
    canonical, transactions, parts, source_pages, source_lines, extracted_pages = [], [], [], [], [], []
    for index, (result, block) in enumerate(zip(results, blocks, strict=True), 1):
        shifted = shift_pages(result, block.start)
        for row in shifted.canonical_transactions or []:
            canonical.append(replace(row, source_sequence=len(canonical)+1,
                source_line_refs=tuple((page + block.start, line) for page, line in row.source_line_refs)))
        transactions.extend(result.transactions)
        source_pages.extend(result.source_page_texts or pages[block.start:block.stop])
        source_lines.extend(shifted.source_layout_lines or [()] * (block.stop-block.start))
        if shifted.source_extraction:
            extracted_pages.extend(shifted.source_extraction.pages)
        parts.append({"id": f"statement-{index}", "document_type": "bank_statement",
            "pages": sorted({page + block.start for part in result.document_parts or []
                             if part.get("included_in_statement") for page in part["pages"]})
                     if result.document_parts else list(range(block.start+1, block.stop+1)), "included_in_statement": True,
            "requires_review": result.parse_metrics.get("export_recommendation") == "review_recommended",
            "period": {"start_date": block.period[0], "end_date": block.period[1], "basis": "document_metadata"} if block.period else None,
            "opening_balance": float(_bounds(result)[0]) if _bounds(result) else None,
            "closing_balance": float(_bounds(result)[1]) if _bounds(result) else None})
        for annex in result.document_parts or []:
            if not annex.get("included_in_statement"):
                parts.append({**annex, "id": f"statement-{index}-{annex['id']}",
                              "pages": [page + block.start for page in annex["pages"]]})
    metrics = dict(results[0].parse_metrics)
    for key in set().union(*(result.parse_metrics for result in results)):
        if key.endswith(("_count", "_transactions_count", "_candidates_count")) or key in {
                "balance_consistency_checked", "balance_consistency_failed", "printed_balance_checks",
                "scheduled_entries_excluded", "non_cash_entries_excluded", "ocr_unresolved_rows"}:
            values = [result.parse_metrics.get(key, 0) for result in results]
            if all(isinstance(value, (int, float)) for value in values):
                metrics[key] = sum(values)
    for key in ("partial_statement", "statement_validation_failed", "opening_balance_ambiguous", "document_period_unavailable",
                "textract_used", "textract_attempted", "native_text_detected"):
        metrics[key] = max(int(result.parse_metrics.get(key, 0)) for result in results)
    balances = [_bounds(result) for result in results]
    contiguous = (all(block.period and block.period[0] <= block.period[1] for block in blocks) and all(balances)
                  and all(result.parse_metrics.get("financial_controls_validated") for result in results))
    if contiguous:
        contiguous = all(date.fromisoformat(right.period[0]) == date.fromisoformat(left.period[1]) + timedelta(days=1)
            and balances[i][1] == balances[i+1][0] for i, (left, right) in enumerate(zip(blocks, blocks[1:])))
    for key in ("verified_opening_balance", "verified_closing_balance", "printed_opening_balance", "printed_closing_balance",
                "derived_opening_balance", "native_cash_opening_balance", "native_cash_closing_balance"):
        metrics.pop(key, None)
    metrics.update(statement_blocks_count=len(blocks), page_count=len(pages),
        statement_no_activity=int(not transactions and all(result.parse_metrics.get("statement_no_activity") for result in results)),
        extracted_char_count=sum(map(len, pages)), flattened_line_count=sum(len(page.splitlines()) for page in pages),
        financial_controls_validated=int(contiguous and all(
            result.parse_metrics.get("financial_controls_validated") for result in results)),
        statement_rows_expected=len(canonical), statement_rows_parsed=len(canonical), statement_row_coverage_rate=1.0,
        balance_bounds_unavailable=int(not contiguous))
    if contiguous:
        metrics.update(verified_opening_balance=float(balances[0][0]), verified_closing_balance=float(balances[-1][1]))
        if any(result.parse_metrics.get("export_recommendation") == "review_recommended" for result in results):
            metrics.update(export_recommendation="review_recommended", export_recommendation_reason=next(
                result.parse_metrics.get("export_recommendation_reason", "statement_block_review")
                for result in results if result.parse_metrics.get("export_recommendation") == "review_recommended"))
    else:
        if canonical:
            canonical[0] = replace(canonical[0], warnings=list(dict.fromkeys([
                *canonical[0].warnings, "manual_review_recommended", "statement_blocks_not_contiguous"])))
        metrics.update(export_recommendation="review_recommended", export_recommendation_reason="statement_blocks_not_contiguous",
                       balance_validation_status="review_required", confidence_band="low")
        for part in parts:
            part["requires_review"] = True
    metrics.update(build_canonical_quality_metrics(canonical))
    extraction = original_extraction
    if extraction is None and len(extracted_pages) == len(pages) and results[0].source_extraction:
        extraction = replace(results[0].source_extraction, document_hash=document_hash, pages=extracted_pages)
    return PdfParseResult(transactions=transactions, canonical_transactions=canonical, layout=results[0].layout,
        extracted_text="\n".join(result.extracted_text for result in results), source_page_texts=tuple(source_pages),
        source_layout_lines=tuple(source_lines) if any(source_lines) else None, source_extraction=extraction,
        parse_metrics=metrics, document_parts=parts)
