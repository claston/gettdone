
"""Santander consolidated scan: cash columns, daily balances and monthly controls."""

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from app.application.errors import InvalidFileContentError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.normalization.date import FULL_MONTH_TO_NUMBER, parse_statement_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.source_regions import evidence_regions

SANTANDER_OCR_LAYOUT = "santander_negocios_empresas_extrato_consolidado_inteligente_conta_corrente_v1"
_MONEY = re.compile(r"^[+\-−]?(?:\d{1,3}(?:\.\d{3})+|\d+)[,.]\d{2}[\-−]?$")
_MESSAGE = "Não foi possível conferir as colunas, os saldos diários e o resumo do extrato Santander. Confira o documento original."
_HEADINGS = re.compile(r"^(?:PIX\b|IOF\b|RESGATE\b|APLICACAO\b|DEBITO\b|TARIF\s*A\b|TED\b|DOC\b|PAGAMENTO\b|PGTO\b|JUROS\b)")


@dataclass
class _Cell:
    page: object
    ref: int
    line: object


@dataclass
class _Movement:
    anchor: _Cell
    day: str
    kind: str
    amount: Decimal | None
    amount_cell: _Cell | None
    balance_cell: _Cell | None
    witnesses: list[_Cell]
    description: str
    warnings: list[str] = field(default_factory=list)


def is_santander_consolidated_scan(extraction):
    text = normalize_upper_text("\n".join(line.text for page in extraction.pages for line in page.lines))
    return "SANTANDER" in text and "EXTRATO CONSOLIDADO INTELIGENTE" in text and "MOVIMENTOS (R$)" in text


def _one(values):
    if len(values) != 1:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return values[0]


def _money(text):
    if not _MONEY.fullmatch(text.strip()):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    value = text.strip().replace("−", "-")
    negative = "-" in value
    value = value.strip("+-")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    else:
        integer, cents = value.rsplit(".", 1)
        value = integer.replace(".", "") + "." + cents
    return Decimal(value) * (-1 if negative else 1)


def _date(text, year):
    try:
        return parse_statement_date(text, fallback_year=year)
    except InvalidFileContentError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _cells(extraction):
    result = []
    for page in extraction.pages:
        number = 1
        for line in sorted(page.lines, key=lambda item: item.line_index):
            if line.text:
                result.append(_Cell(page, number, line))
                number += len(line.text.splitlines())
    return result


def _printed(cell):
    return not any(word.id in cell.line.word_ids and word.text_type == "HANDWRITING" for word in cell.page.words)


def _aligned_value(label, cells, left, right=1):
    values = [cell for cell in cells if cell.page.page_number == label.page.page_number and cell.line.bbox
              and left <= cell.line.bbox["left"] < right
              and abs(cell.line.bbox["top"] - label.line.bbox["top"]) <= .006]
    value = _one(values)
    if not _printed(value):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return value, _money(value.line.text)


def _period(text):
    periods = {(int(match[2]), FULL_MONTH_TO_NUMBER[match[1]])
               for match in re.finditer(r"\b(" + "|".join(FULL_MONTH_TO_NUMBER) + r")/(20\d{2})\b", normalize_upper_text(text))}
    year, month = _one(list(periods))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def extract_santander_summary_opening(text):
    """Read only the previous month's cash balance, never an available/investment balance."""
    try:
        start, _ = _period(text)
    except OcrFinancialAmbiguityError:
        return None
    label = "SALDO DE CONTA CORRENTE EM " + (start - timedelta(days=1)).strftime("%d/%m")
    lines = text.splitlines()
    values = {_money(lines[index + 1]) for index, line in enumerate(lines[:-1])
              if normalize_upper_text(line) == label and _MONEY.fullmatch(lines[index + 1].strip())}
    return float(next(iter(values))) if len(values) == 1 else None


def _summary(cells):
    start, end = _period("\n".join(cell.line.text for cell in cells))
    labels = {
        "opening": "SALDO DE CONTA CORRENTE EM " + (start - timedelta(days=1)).strftime("%d/%m"),
        "closing": "SALDO DE CONTA CORRENTE EM " + end.strftime("%d/%m"),
        "credit": "TOTAL DE CREDITOS", "debit": "TOTAL DE DEBITOS",
    }
    controls, witnesses = {}, []
    for kind, label in labels.items():
        source = _one([cell for cell in cells if normalize_upper_text(cell.line.text) == label and cell.line.bbox])
        value_cell, value = _aligned_value(source, cells, .55, .8)
        if not _printed(source):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        controls[kind] = value
        witnesses.extend((source, value_cell))
    if controls["credit"] < 0 or controls["debit"] < 0:
        raise PdfFinancialValidationError(_MESSAGE)
    if controls["opening"] + controls["credit"] - controls["debit"] != controls["closing"]:
        financial_mismatch(_MESSAGE)
    return start, end, controls, witnesses


def _read_movements(extraction, cells, start, end, controls):
    rows, orphan_marks = [], []
    opened = closed = False
    day = None
    for page in extraction.pages:
        page_cells = [cell for cell in cells if cell.page.page_number == page.page_number]
        movement_headers = [cell for cell in page_cells if cell.line.bbox and normalize_upper_text(cell.line.text) == "MOVIMENTOS (R$)"]
        if not movement_headers:
            continue
        header_y = _one(movement_headers).line.bbox["top"]
        headers = {normalize_upper_text(cell.line.text): cell for cell in page_cells if cell.line.bbox
                   and abs(cell.line.bbox["top"] - header_y) < .03
                   and normalize_upper_text(cell.line.text) in {
                       "DATA", "DESCRICAO", "MOVIMENTOS (R$)", "CREDITOS", "DEBITOS", "SALDO (R$)",
                   }}
        if closed or len(headers) != 6:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        top = max(cell.line.bbox["top"] for cell in headers.values())
        if top - min(cell.line.bbox["top"] for cell in headers.values()) > .04:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        credit_x, debit_x, balance_x = [headers[key].line.bbox["left"] for key in ("CREDITOS", "DEBITOS", "SALDO (R$)")]
        if not headers["DATA"].line.bbox["left"] < headers["DESCRICAO"].line.bbox["left"] < credit_x < debit_x < balance_x:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        debit_left = (credit_x + debit_x) / 2
        balance_left = (debit_x + balance_x) / 2
        amount_left = credit_x - .065
        desc_left = (headers["DATA"].line.bbox["left"] + headers["DESCRICAO"].line.bbox["left"]) / 2
        description_right = amount_left - .12
        positioned = [cell for cell in page_cells if cell.line.bbox and cell.line.bbox["top"] > top + .005
                      and not re.fullmatch(r"PAG(?:INA|\.)?\s*:?\s*\d+(?:\s*(?:/|DE)\s*\d+)?",
                                           normalize_upper_text(cell.line.text))]
        if any(not cell.line.bbox and _MONEY.fullmatch(cell.line.text.strip()) for cell in page_cells):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        closing_labels = [cell for cell in positioned if normalize_upper_text(cell.line.text) == "SALDO EM " + end.strftime("%d/%m")]
        terminal = _one(closing_labels).line.bbox["top"] if closing_labels else .94
        positioned = [cell for cell in positioned if cell.line.bbox["top"] <= terminal + .006]
        opening_text = "SALDO EM " + (start - timedelta(days=1)).strftime("%d/%m")
        opening_labels = [cell for cell in positioned if normalize_upper_text(cell.line.text) == opening_text]
        used = set()
        for label in [*opening_labels, *closing_labels]:
            value_cell, value = _aligned_value(label, positioned, balance_left)
            kind = "opening" if label in opening_labels else "closing"
            if kind == "opening" and opened:
                raise PdfFinancialValidationError(_MESSAGE)
            if value != controls[kind]:
                financial_mismatch(_MESSAGE)
            opened |= kind == "opening"
            closed |= kind == "closing"
            used.add(value_cell.line.id)
        if not opened:
            raise PdfFinancialValidationError(_MESSAGE)
        descriptions = [cell for cell in positioned if desc_left <= cell.line.bbox["left"] < description_right
                        and not normalize_upper_text(cell.line.text).startswith("SALDO EM ")]
        amounts = [cell for cell in positioned if amount_left <= cell.line.bbox["left"] < balance_left]
        anchors = [cell for cell in descriptions if _HEADINGS.match(normalize_upper_text(cell.line.text))]
        anchors.sort(key=lambda cell: cell.line.bbox["top"])
        dates = [cell for cell in positioned if cell.line.bbox["left"] < desc_left]
        if any(not re.fullmatch(r"\d{2}/\d{2}", cell.line.text.strip()) for cell in dates):
            raise PdfFinancialValidationError(_MESSAGE)
        for index, anchor in enumerate(anchors):
            y = anchor.line.bbox["top"]
            available_dates = [cell for cell in dates if cell.line.bbox["top"] <= y + .004]
            if available_dates:
                date_cell = max(available_dates, key=lambda item: item.line.bbox["top"])
                day = _date(date_cell.line.text, start.year)
            else:
                date_cell = None
            if day is None or not start.isoformat() <= day <= end.isoformat() or rows and day < rows[-1].day:
                raise PdfFinancialValidationError(_MESSAGE)
            next_y = anchors[index + 1].line.bbox["top"] if index + 1 < len(anchors) else terminal
            candidates = [cell for cell in amounts if abs(cell.line.bbox["top"] - y) <= .006]
            amount_cell = _one(candidates) if candidates else None
            if amount_cell is None:
                if normalize_upper_text(anchor.line.text) != "IOF ADICIONAL AUTOMATICO":
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                kind, amount = "debit", None
            else:
                if not _printed(amount_cell):
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                kind = "credit" if amount_cell.line.bbox["left"] < debit_left else "debit"
                amount = _money(amount_cell.line.text)
                if kind == "credit" and amount < 0 or kind == "debit" and "+" in amount_cell.line.text:
                    raise PdfFinancialValidationError(_MESSAGE)
                amount = abs(amount) if kind == "credit" else -abs(amount)
                used.add(amount_cell.line.id)
            balance_candidates = [cell for cell in positioned if cell.line.bbox["left"] >= balance_left
                                  and abs(cell.line.bbox["top"] - y) <= .008]
            balance_cell = _one(balance_candidates) if balance_candidates else None
            if balance_cell:
                if not _printed(balance_cell):
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                _money(balance_cell.line.text)
                used.add(balance_cell.line.id)
            details = [cell for cell in descriptions if y <= cell.line.bbox["top"] < next_y]
            witnesses = [*details, *([date_cell] if date_cell else []),
                         *([amount_cell] if amount_cell else []), *([balance_cell] if balance_cell else [])]
            rows.append(_Movement(anchor, day, kind, amount, amount_cell, balance_cell, witnesses,
                                  " ".join(cell.line.text for cell in details)))
        for cell in positioned:
            if cell.line.bbox["left"] >= amount_left and cell.line.id not in used:
                _money(cell.line.text)
                orphan_marks.append(cell)
    if not closed or not rows:
        raise PdfFinancialValidationError(_MESSAGE)
    return rows, orphan_marks


def _recover_and_validate(rows, marks, controls, summary_witnesses):
    groups = []
    for row in rows:
        if not groups or groups[-1][0].day != row.day or groups[-1][-1].balance_cell is not None:
            groups.append([])
        groups[-1].append(row)
    totals = {kind: sum((abs(row.amount) for row in rows if row.kind == kind and row.amount is not None), Decimal(0))
              for kind in ("credit", "debit")}
    missing = [row for row in rows if row.amount is None]
    previous = controls["opening"]
    previous_witness = []
    recovered = []
    for group in groups:
        if group[-1].balance_cell is None:
            if any(row.amount is None for row in group):
                raise OcrFinancialAmbiguityError(_MESSAGE)
            financial_mismatch(_MESSAGE)
            closing = sum((row.amount for row in group), previous)
        else:
            closing = _money(group[-1].balance_cell.line.text)
        gap = closing - previous - sum((row.amount for row in group if row.amount is not None), Decimal(0))
        if gap:
            candidates = []
            for row in group:
                if row.amount is None:
                    if len(missing) == 1 and gap < 0 and -gap == controls["debit"] - totals["debit"]:
                        candidates.append((row, gap))
                elif row.kind == "credit" and row.amount_cell.line.confidence is not None and row.amount_cell.line.confidence < 85:
                    new_amount = row.amount + gap
                    old_digits = format(row.amount, ".2f").replace(".", "")
                    new_digits = format(new_amount, ".2f").replace(".", "")
                    if (gap > 0 and gap == controls["credit"] - totals["credit"] and new_digits.endswith(old_digits)
                            and len(new_digits) == len(old_digits) + 1 and new_digits[0] in "123456789"):
                        candidates.append((row, new_amount))
            if not candidates and all(row.amount is not None for row in group):
                financial_mismatch(_MESSAGE)
            else:
                row, amount = _one(candidates)
                totals[row.kind] += abs(amount) - abs(row.amount or Decimal(0))
                row.amount = amount
                row.warnings.append("ocr_amount_recovered_from_summary")
                row.witnesses.extend([*summary_witnesses, *previous_witness, *[cell for item in group for cell in item.witnesses]])
                recovered.append(row)
        if sum((row.amount or Decimal(0) for row in group), previous) != closing:
            financial_mismatch(_MESSAGE)
        previous = closing
        previous_witness = [group[-1].balance_cell] if group[-1].balance_cell else []
    if any(row.amount is None for row in missing):
        raise PdfFinancialValidationError(_MESSAGE)
    if (totals["credit"] != controls["credit"] or totals["debit"] != controls["debit"]
            or previous != controls["closing"]):
        financial_mismatch(_MESSAGE)
    for mark in marks:
        matching = [row for row in recovered if row.kind == "credit" and row.anchor.page.page_number == mark.page.page_number
                    and row.anchor.line.bbox["top"] + .006 < mark.line.bbox["top"] < row.anchor.line.bbox["top"] + .018
                    and abs(mark.line.bbox["left"] - row.amount_cell.line.bbox["left"]) < .025
                    and _money(mark.line.text) == row.amount]
        row = _one(matching)
        row.witnesses.append(mark)  # Audit only: the annotation never supplies an amount.
    return recovered


def parse_santander_ocr(extraction):
    cells = _cells(extraction)
    start, end, controls, witnesses = _summary(cells)
    rows, marks = _read_movements(extraction, cells, start, end, controls)
    recovered = _recover_and_validate(rows, marks, controls, witnesses)
    parsed = []
    for row in rows:
        regions = []
        for page in extraction.pages:
            sources = {cell.line.id: cell.line for cell in row.witnesses if cell.page.page_number == page.page_number}
            regions.extend(evidence_regions(page.page_number, list(sources.values()), words=page.words))
        result = build_parsed_transaction(
            date=row.day, description=row.description, amount=float(row.amount),
            source_page=row.anchor.page.page_number, source_line=row.anchor.ref,
            running_balance=float(_money(row.balance_cell.line.text)) if row.balance_cell else None,
            has_explicit_amount_sign=True, source_regions=regions,
            source_line_refs=tuple(dict.fromkeys((cell.page.page_number, cell.ref) for cell in row.witnesses)),
        )
        parsed.append(replace(result, warnings=row.warnings))
    return parsed, {"financial_controls_validated": 1, "ocr_amounts_recovered_from_summary": len(recovered),
                    "ocr_off_row_marks_excluded": len(marks)}
