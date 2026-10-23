import re
from dataclasses import dataclass, field

from app.application.document_extraction_models import ExtractedLine, ExtractedPage
from app.application.normalization.date import infer_default_statement_year
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction
from app.application.source_regions import evidence_regions

_AMOUNT = re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[+-]?$")
_DATE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?$")


@dataclass
class MonthlyOcrResult:
    rows: list[_ParsedTransaction] = field(default_factory=list)
    unresolved_rows: int = 0
    annotations_detected: int = 0
    opening_balance_ambiguous: bool = False


def parse_itau_monthly_ocr(pages: list[ExtractedPage]) -> MonthlyOcrResult:
    """Read this layout's printed financial columns; never use pen marks as amount signs."""
    result = MonthlyOcrResult()
    year = infer_default_statement_year([line.text for page in pages for line in page.lines])
    if year is None:
        years = {int(value) for page in pages for line in page.lines for value in re.findall(r"\b(20\d{2})\b", line.text)}
        year = next(iter(years)) if len(years) == 1 else None
    current_date = None
    for page in pages:
        columns = _columns(page.lines)
        if columns is None:
            if any(normalize_upper_text(line.text) == "ENTRADAS R$" for line in page.lines):
                result.unresolved_rows += 1
            continue
        date_x, desc_x, credit_x, debit_x, balance_x, header_y = columns
        desc_left = (date_x + desc_x) / 2
        credit_left = credit_x - 0.025
        debit_left = debit_x - 0.025
        balance_left = balance_x - 0.025
        positioned = [line for line in page.lines if line.bbox and line.bbox["top"] > header_y + 0.01]
        descriptions = sorted([
            line for line in positioned if desc_left <= line.bbox["left"] < credit_left
        ], key=lambda line: line.bbox["top"])
        terminal_rows = [line for line in descriptions if normalize_upper_text(line.text).startswith(
            ("SALDO EM C/C", "SALDO FINAL", "TOTALIZADOR DE APLICACOES")
        )]
        end_top = min((line.bbox["top"] for line in terminal_rows), default=1.0)
        descriptions = [line for line in descriptions if line.bbox["top"] < end_top]
        date_lines = [line for line in positioned if line.bbox["left"] < desc_left and _DATE.fullmatch(line.text.strip())]
        # Words split OCR lines that accidentally merge credit, debit and handwritten notes.
        values = page.words if page.words else page.lines
        amounts = [value for value in values if value.bbox and header_y + 0.01 < value.bbox["top"] < end_top
                   and _AMOUNT.fullmatch(value.text.strip())]
        result.annotations_detected += sum(
            word.text_type == "HANDWRITING" or (
                bool(word.bbox) and credit_left <= word.bbox["left"] < balance_left
                and normalize_upper_text(word.text).startswith("NOTA")
            ) for word in page.words
        )
        used_amounts: set[str] = set()
        unresolved_before_page = result.unresolved_rows
        for description in descriptions:
            label = normalize_upper_text(description.text)
            top = description.bbox["top"]
            if label.startswith("SALDO ANTERIOR"):
                # This layout can print a consolidated opening next to current-account movements.
                result.opening_balance_ambiguous = True
                continue
            if label.startswith(("SALDO", "DESCRICAO", "(CREDITOS)", "(DEBITOS)")):
                continue
            nearest_dates = [line for line in date_lines if line.bbox["top"] <= top + 0.006]
            if nearest_dates:
                current_date = max(nearest_dates, key=lambda line: line.bbox["top"]).text.strip()
            tolerance = max(0.004, description.bbox.get("height", 0.008) * 0.8)
            candidates = [
                value for value in amounts if credit_left <= value.bbox["left"] < balance_left
                and abs(value.bbox["top"] - top) <= tolerance
            ]
            if year is None or current_date is None or len(candidates) != 1 or candidates[0].id in used_amounts:
                result.unresolved_rows += 1
                continue
            value = candidates[0]
            used_amounts.add(value.id)
            amount = abs(parse_pdf_amount(value.text))
            if value.bbox["left"] >= debit_left:
                amount = -amount
            # A handwritten financial value needs review; preserving it is safer than deleting it.
            if getattr(value, "text_type", None) == "HANDWRITING":
                result.unresolved_rows += 1
                continue
            balances = [
                item for item in amounts if item.bbox["left"] >= balance_left
                and abs(item.bbox["top"] - top) <= tolerance
            ]
            result.rows.append(build_parsed_transaction(
                date=parse_row_date(current_date, fallback_year=year), description=description.text,
                amount=amount, source_page=page.page_number, source_line=description.line_index,
                running_balance=parse_pdf_amount(balances[0].text) if len(balances) == 1 else None,
                has_explicit_amount_sign=True,
                source_regions=evidence_regions(page.page_number, [description, value, *balances], words=page.words),
            ))
        unused_amounts = sum(
            credit_left <= value.bbox["left"] < balance_left and value.id not in used_amounts for value in amounts
        )
        result.unresolved_rows = unresolved_before_page + max(result.unresolved_rows - unresolved_before_page, unused_amounts)
        if terminal_rows:
            return result
    return result


def _columns(lines: list[ExtractedLine]) -> tuple[float, float, float, float, float, float] | None:
    labels = {"DATA": None, "DESCRICAO": None, "ENTRADAS R$": None, "SAIDAS R$": None, "SALDO R$": None}
    for line in sorted(lines, key=lambda item: item.bbox["top"] if item.bbox else 1.0):
        label = normalize_upper_text(line.text)
        if label in labels and line.bbox and labels[label] is None:
            labels[label] = line.bbox
    if any(box is None for box in labels.values()):
        return None
    boxes = list(labels.values())
    if max(box["top"] for box in boxes) - min(box["top"] for box in boxes) > 0.02:
        return None
    positions = tuple(box["left"] for box in boxes)
    if positions != tuple(sorted(positions)):
        return None
    return (*positions, max(box["top"] for box in boxes))
