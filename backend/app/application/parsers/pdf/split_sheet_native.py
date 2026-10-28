"""Native Excel column pages and BASA icon signs, with complete ledger checks."""

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from io import BytesIO
from math import hypot

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.reader import open_pdf_reader
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import SourceRegion

BB_SHEET = "bb_excel_horizontal_pages_v1"
BILLING_SHEET = "billing_excel_horizontal_pages_v1"
AMAZON = "banco_amazonia_native_icon_signs_v1"
_BR = re.compile(rf"(?<![\w.,])(-?{NUMBER})(?![\d,.])")
_DOT = re.compile(r"-?\d+(?:\.\d{1,2})?")
_DATE = re.compile(rf"^({DATE})\b")


@dataclass
class Cell:
    text: str
    x: float
    y: float
    size: float


@dataclass
class Line:
    cells: list
    page: int
    number: int
    width: float
    height: float

    @property
    def text(self):
        return " ".join(c.text for c in self.cells)

    @property
    def y(self):
        return self.cells[0].y


def _kind(pages):
    text = upper("\n".join(pages))
    if "BANCO DA AMAZONIA" in text and "CONSULTA DE EXTRATO" in text:
        return AMAZON
    if "TIPO DE TRANSACAO" in text and "FATURA DA COBRANCA" in text and "SALDO INICIAL" in text and "PERIODO A PARTIR DE" in text:
        return BILLING_SHEET
    if "BB RENDE FACIL" in text and "LANCAMENTO" in text and "DETALHES" in text and "SALDO ANTERIOR" in text and "TIPO LANCAMENTO" in text:
        return BB_SHEET
    return None


def _read(raw):
    """Use original text objects, before narrow Excel cells overlap in plain text."""
    pages = []
    for index, page in enumerate(open_pdf_reader(BytesIO(raw)).pages, 1):
        if page.rotation or float(page.mediabox.left) or float(page.mediabox.bottom):
            fail()
        cells = []
        last_position = None

        def before(operator, operands, cm, tm):
            nonlocal last_position
            if operator in {b"Tj", b"TJ", b"'", b'"'}:
                last_position = (list(cm), list(tm))

        def visitor(text, cm, tm, font, size):
            text = " ".join(text.split())
            if not text:
                return
            # pypdf can flush a standalone text object after ET reset its matrix.
            if tm[4] == tm[5] == 0 and last_position is not None:
                cm, tm = last_position
            if abs(cm[1]) > 0.001 or abs(cm[2]) > 0.001 or abs(tm[1]) > 0.001 or abs(tm[2]) > 0.001:
                fail()
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            size = abs(size) * hypot(cm[0], cm[1])
            if size <= 0:
                fail()
            # HTML bold emulation draws the same text twice, <1 glyph apart.
            if not any(c.text == text and abs(c.x - x) < size * 0.15 and abs(c.y - y) < size * 0.15 for c in cells):
                cells.append(Cell(text, x, y, size))

        page.extract_text(visitor_text=visitor, visitor_operand_before=before)
        groups = []
        for cell in sorted(cells, key=lambda c: (-c.y, c.x)):
            if groups and abs(groups[-1][0].y - cell.y) < min(groups[-1][0].size, cell.size) * 0.2:
                groups[-1].append(cell)
            else:
                groups.append([cell])
        pages.append(
            [
                Line(sorted(group, key=lambda c: c.x), index, n, float(page.mediabox.width), float(page.mediabox.height))
                for n, group in enumerate(groups, 1)
            ]
        )
    return pages


def _regions(lines):
    result = []
    for line in lines:
        size = max(c.size for c in line.cells)
        left = max(0, min(c.x for c in line.cells))
        right = min(line.width, max(c.x + len(c.text) * c.size * 0.65 for c in line.cells))
        bottom = max(0, line.y - size * 0.3)
        top = min(line.height, line.y + size * 1.1)
        if right > left and top > bottom:
            result.append(
                SourceRegion(line.page, left / line.width, 1 - top / line.height, (right - left) / line.width, (top - bottom) / line.height)
            )
    return result


def _make(lines, date, description, amount, balance=None, reference=None):
    first = lines[0]
    row = _row(first.page, first.number, date, description, amount, balance, reference)
    return replace(row, source_line_refs=tuple((line.page, line.number) for line in lines), source_regions=_regions(lines))


def _control(rows, line, value, running):
    if value != running or not rows:
        fail()
    old = rows[-1]
    rows[-1] = replace(
        old,
        running_balance=float(value),
        source_line_refs=(*old.source_line_refs, (line.page, line.number)),
        source_regions=[*old.source_regions, *_regions([line])],
    )


def _paired(pages):
    if len(pages) % 2 or not pages:
        fail()
    half = len(pages) // 2
    return zip(pages[:half], pages[half:])


def _bb(pages):
    rows = []
    opening = running = closing = None
    previous = None
    checks = 0
    for left, right in _paired(pages):
        dates = [line for line in left if _DATE.match(line.text)]
        values = [line for line in right if _BR.search(line.text)]
        if len(dates) != len(values) or not dates:
            fail()
        for source, value_line in zip(dates, values):
            if (
                source.width != value_line.width
                or source.height != value_line.height
                or abs(source.y - value_line.y) > max(c.size for c in source.cells) * 0.2
            ):
                fail()
            match = _DATE.match(source.text)
            date = day(match[1])
            if previous and date < previous:
                fail()
            previous = date
            description = source.text[match.end() :].strip()
            normalized = upper(description).replace(" ", "")
            amounts = list(_BR.finditer(value_line.text))
            if len(amounts) != 1 or closing is not None:
                fail()
            value = money(amounts[0][1])
            if normalized == "SALDOANTERIOR":
                if opening is not None or rows:
                    fail()
                opening = running = value
                checks += 1
            elif normalized in {"SALDODODIA", "SALDO"}:
                _control(rows, value_line, value, running)
                checks += 1
                if normalized == "SALDO":
                    closing = value
            else:
                sign = upper(value_line.text[amounts[0].end() :]).strip()
                if running is None or sign not in {"ENTRADA", "SAIDA"} or (value < 0) != (sign == "SAIDA"):
                    fail()
                reference = value_line.text[: amounts[0].start()].strip()
                if not re.fullmatch(r"\d+", reference):
                    fail()
                running += value
                rows.append(_make([source, value_line], date, description, value, reference=reference))
    if not rows or closing is None or checks < 3:
        fail()
    return VerifiedStatement(rows, BB_SHEET, opening, closing, printed_balance_checks=checks)


def _billing(pages):
    rows = []
    opening = running = closing = None
    checks = 0
    previous = None
    headers = next((line for line in pages[0] if any(c.text == "Valor" for c in line.cells)), None)
    if headers is None:
        fail()
    labels = {upper(c.text): c.x for c in headers.cells}
    required = ["DATA", "TRANSACAO", "TIPO DE TRANSACAO", "TRANSACAO ESTORNADA", "DESCRICAO", "VALOR", "SALDO", "FATURA DO PARCELAMENTO"]
    if any(k not in labels for k in required):
        fail()
    period = re.search(r"PERIODO A PARTIR DE (\d{2}/\d{2}/\d{4}) ATE (\d{2}/\d{2}/\d{4})", upper(" ".join(line.text for line in pages[0])))
    start, end = optional_document_period(period)

    def field(line, key, next_key):
        return " ".join(c.text for c in line.cells if labels[key] - 0.5 * c.size <= c.x < labels[next_key] - 0.5 * c.size)

    for left, right in _paired(pages):
        dates = [line for line in left if _DATE.match(line.text)]
        types = [line for line in right if upper(line.text) in {"CREDITO", "DEBITO"}]
        if len(dates) != len(types) or not dates:
            fail()
        matched = 0
        for line in left:
            normalized = upper(line.text)
            dated = _DATE.match(line.text)
            if "SALDO INICIAL" in normalized or "SALDO FINAL" in normalized:
                cell = field(line, "SALDO", "FATURA DO PARCELAMENTO")
                if not _DOT.fullmatch(cell):
                    fail()
                value = Decimal(cell)
                if "SALDO INICIAL" in normalized:
                    if opening is not None or rows:
                        fail()
                    opening = running = value
                else:
                    if closing is not None:
                        fail()
                    _control(rows, line, value, running)
                    closing = value
                checks += 1
                continue
            if not dated:
                if any(
                    _DOT.fullmatch(field(line, key, next_key))
                    for key, next_key in (("VALOR", "SALDO"), ("SALDO", "FATURA DO PARCELAMENTO"))
                ):
                    fail()
                continue
            date = day(dated[1])
            if running is None or closing is not None or not period_contains(date, start, end) or previous and date < previous:
                fail()
            previous = date
            type_line = types[matched]
            matched += 1
            if abs(line.y - type_line.y) > max(c.size for c in line.cells) * 0.2:
                fail()
            amount = field(line, "VALOR", "SALDO")
            balance = field(line, "SALDO", "FATURA DO PARCELAMENTO")
            if not _DOT.fullmatch(amount) or not _DOT.fullmatch(balance):
                fail()
            amount, balance = Decimal(amount), Decimal(balance)
            if (amount < 0) != (upper(type_line.text) == "DEBITO") or running + amount != balance:
                fail()
            running = balance
            # Keep the printed rounded identifier as evidence, never expand it.
            description = " ".join(c.text for c in line.cells if labels["TRANSACAO"] - 0.5 * c.size <= c.x < labels["VALOR"] - 0.5 * c.size)
            rows.append(_make([line, type_line], date, description, amount, balance))
            checks += 1
    if not rows or closing is None:
        fail()
    return VerifiedStatement(rows, BILLING_SHEET, opening, closing, printed_balance_checks=checks)


def _amazon(pages):
    rows = []
    opening = running = closing = None
    checks = 0
    previous = None
    summary = False
    for lines in pages:
        for line in lines:
            text = upper(line.text)
            date_match = next(((_DATE.fullmatch(c.text), c) for c in line.cells if _DATE.fullmatch(c.text)), None)
            if date_match and any(c.x < date_match[1].x and c.text != "\uf002" for c in line.cells):
                date_match = None
            currencies = [c for c in line.cells if re.fullmatch(rf"R\$\s*{NUMBER}", c.text)]
            signs = [c for c in line.cells if c.text in {"\uf055", "\uf056"}]
            opening_label = "SALDO ANTERIOR" in text
            daily = "SALDO DO DIA" in text
            final = "SALDO ATUAL" in text
            available = "SALDO DISPONIVEL" in text
            if text == "SALDOS":
                summary = True
                continue
            if not (date_match or opening_label or daily or final or available):
                if currencies:
                    fail()
                continue
            if len(currencies) != 1 or len(signs) != 1 or signs[0].x <= currencies[0].x:
                fail()
            value = money(re.sub(r"^R\$\s*", "", currencies[0].text)) * (-1 if signs[0].text == "\uf056" else 1)
            if opening_label:
                if opening is not None or rows:
                    fail()
                opening = running = value
                checks += 1
            elif daily or final or available:
                if (final or available) and not summary:
                    fail()
                _control(rows, line, value, running)
                checks += 1
                if final:
                    if closing is not None:
                        fail()
                    closing = value
            else:
                date = day(date_match[0][1])
                if running is None or summary or previous and date < previous:
                    fail()
                previous = date
                date_cell = date_match[1]
                details = [c.text for c in line.cells if date_cell.x < c.x < currencies[0].x]
                if len(details) < 2 or not details[0].isdigit():
                    fail()
                running += value
                rows.append(_make([line], date, " ".join(details[1:]), value, reference=details[0]))
    if not rows or closing is None or checks < 3 or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, AMAZON, opening, closing, printed_balance_checks=checks)


def parse_split_sheet_native(raw, pages):
    kind = _kind(pages)
    if kind is None:
        return None
    physical = _read(raw)
    statement = {BB_SHEET: _bb, BILLING_SHEET: _billing, AMAZON: _amazon}[kind](physical)
    return statement, ["\n".join(line.text for line in page) for page in physical]
