"""One financial policy over reconstructed OCR rows, independent of reader/layout.

Experimental single-block cash tables with explicit full dates and signed amounts.
Other financial document types require explicit semantics, never a cash coercion.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.application.ocr_structure import OcrIssue, normalized_label, projected_y

_MONEY = re.compile(r"(?:R\$\s*)?([+-]?)\s*((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s*([+-]?)")


@dataclass(frozen=True)
class CashTablePolicy:
    opening_labels: tuple[str, ...]
    closing_labels: tuple[str, ...]
    date_column: str = "date"
    description_column: str = "description"
    amount_column: str = "amount"
    date_format: str = "%d/%m/%Y"
    minimum_critical_confidence: float = 0.90


@dataclass(frozen=True)
class OcrCashMovement:
    date: str
    description: str
    amount: Decimal
    source_page: int
    source_row: int
    source_word_ids: tuple[str, ...]
    source_regions: tuple


@dataclass(frozen=True)
class OcrCashResult:
    movements: tuple[OcrCashMovement, ...]
    opening: Decimal | None
    closing: Decimal | None
    balance_matches: bool
    requires_review: bool
    issues: tuple[OcrIssue, ...]
    unresolved_rows: tuple


def _amount(text):
    match = _MONEY.fullmatch(text.strip())
    if not match or (match[1] and match[3] and match[1] != match[3]):
        return None
    value = Decimal(match[2].replace(".", "").replace(",", "."))
    return -value if "-" in (match[1], match[3]) else value


def _controls(extraction, structure, labels):
    found = []
    wanted = {normalized_label(label) for label in labels}
    for page in extraction.pages:
        slope = structure.page_slopes.get(page.page_number, 0.0)
        for line in page.lines:
            if normalized_label(line.text) not in wanted or not line.bbox:
                continue
            aligned = [
                word
                for word in page.words
                if word.bbox
                and _amount(word.text) is not None
                and word.bbox["left"] > line.bbox["left"] + line.bbox["width"]
                and abs(projected_y(word, slope) - projected_y(line, slope)) <= max(word.bbox["height"], line.bbox["height"]) * 0.8
            ]
            if len(aligned) == 1:
                found.append((_amount(aligned[0].text), aligned[0].confidence, page.page_number))
    return found


def interpret_cash_table(extraction, structure, policy: CashTablePolicy) -> OcrCashResult:
    issues, movements, unresolved = list(structure.issues), [], []
    for row in structure.rows:
        date_cell, description_cell, amount_cell = [
            row.cells.get(key) for key in (policy.date_column, policy.description_column, policy.amount_column)
        ]
        date = None
        try:
            date = datetime.strptime(date_cell.text, policy.date_format).date().isoformat() if date_cell else None
        except ValueError:
            pass
        amount = _amount(amount_cell.text) if amount_cell else None
        codes = []
        if date is None:
            codes.append("unreadable_date")
        if amount is None:
            codes.append("unreadable_amount")
        if not description_cell or not description_cell.text.strip():
            codes.append("unreadable_description")
        for code in codes:
            issues.append(OcrIssue(code, row.page_number, row.row_number))
        if codes:
            unresolved.append(row)
            continue
        for cell in (date_cell, amount_cell):
            if cell.confidence is None or cell.confidence < policy.minimum_critical_confidence:
                issues.append(OcrIssue("critical_confidence_unconfirmed", row.page_number, row.row_number))
        identifiers = tuple(dict.fromkeys(identifier for cell in row.cells.values() for identifier in cell.word_ids))
        regions = tuple(dict.fromkeys(region for cell in row.cells.values() for region in cell.source_regions))
        movements.append(OcrCashMovement(date, description_cell.text, amount, row.page_number, row.row_number, identifiers, regions))
    openings = _controls(extraction, structure, policy.opening_labels)
    closings = _controls(extraction, structure, policy.closing_labels)
    if len(openings) > 1 or len(closings) > 1:
        issues.append(OcrIssue("multiple_financial_blocks", 0))
    opening = openings[0][0] if len(openings) == 1 else None
    closing = closings[0][0] if len(closings) == 1 else None
    for _, confidence, page_number in [*openings, *closings]:
        if confidence is None or confidence / 100 < policy.minimum_critical_confidence:
            issues.append(OcrIssue("control_confidence_unconfirmed", page_number))
    if opening is None or closing is None:
        issues.append(OcrIssue("printed_controls_unconfirmed", 0))
    matches = bool(
        opening is not None
        and closing is not None
        and not unresolved
        and opening + sum((row.amount for row in movements), Decimal(0)) == closing
    )
    if not matches:
        issues.append(OcrIssue("financial_validation_unconfirmed", 0))
    if len(movements) != structure.expected_rows:
        issues.append(OcrIssue("row_coverage_mismatch", 0))
    return OcrCashResult(tuple(movements), opening, closing, matches, bool(issues), tuple(issues), tuple(unresolved))
