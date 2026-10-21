"""Read native monthly Itaú columns and validate cash separately from automatic investments."""
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK, _find_boxes, _TextBox
from app.application.source_regions import SourceRegion

LAYOUT = "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1"
PARSER = "layout_specific_itau_monthly_native"
_MONEY = re.compile(r"(?<![\d,.])[+\-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}-?(?![\d,.])")
_DATE = re.compile(r"(?<!\d)\d{2}/\d{2}(?:/\d{2,4})?(?!\d)")
_COUNTER = re.compile(r"(\d{3})\|(\d{3})(?!\d)")
_MESSAGE = "Não foi possível conferir todas as colunas, os saldos e as aplicações automáticas do extrato mensal Itaú."


def _fail():
    raise PdfFinancialValidationError(_MESSAGE)


def _money(text):
    return Decimal(str(parse_pdf_amount(text)))


def _boxes(textpage, characters, pattern):
    boxes = []
    for match in pattern.finditer(characters):
        rects = [textpage.get_charbox(index) for index in range(match.start(), match.end())]
        boxes.append(_TextBox(match[0], min(r[0] for r in rects), min(r[1] for r in rects),
                              max(r[2] for r in rects), max(r[3] for r in rects)))
    return boxes


def _columns(tp):
    labels = ("data", "descrição", "entradas R$", "saídas R$", "saldo")
    for day in _find_boxes(tp, labels[0]):
        result = [day]
        for label in labels[1:]:
            result.append(next((box for box in _find_boxes(tp, label)
                                if abs(box.bottom - day.bottom) < 3 and box.left > result[-1].left), None))
            if result[-1] is None:
                break
        if len(result) == 5 and result[-1] is not None:
            return result
    return None


def _summary(tp, money, columns):
    dates = sorted((box for box in _find_boxes(tp, "saldo em") if box.bottom > columns[0].top), key=lambda box: box.left)
    if len(dates) != 2:
        _fail()
    amounts, days = [], []
    for index, label in enumerate(dates):
        right = dates[index + 1].left - 2 if index == 0 else 10000
        nearby = [box for box in money if label.left - 5 <= box.left < right and -25 <= box.bottom - label.bottom <= 3]
        if not nearby:
            _fail()
        amounts.append(_money(min(nearby, key=lambda box: abs(box.bottom - label.bottom)).text))
        raw = tp.get_text_bounded(label.left - 1, label.bottom - 2, right, label.top + 2)
        match = re.search(r"\d{2}/\d{2}/\d{2,4}", raw)
        if match is None:
            _fail()
        try:
            days.append(datetime.strptime(match[0], "%d/%m/%y" if len(match[0]) == 8 else "%d/%m/%Y").date())
        except ValueError as exc:
            raise PdfFinancialValidationError(_MESSAGE) from exc
    labels = []
    for text in ("entradas (créditos)", "saídas (débitos)"):
        candidates = [box for box in _find_boxes(tp, text) if box.bottom > columns[0].top]
        if not candidates:
            _fail()
        labels.append(max(candidates, key=lambda box: box.bottom))
    totals = []
    for index, label in enumerate(labels):
        bottom = labels[index + 1].top if index == 0 else columns[0].top
        candidates = [box for box in money if box.left >= label.left - 2 and bottom < box.bottom <= label.top]
        if not candidates:
            _fail()
        totals.append(_money(min(candidates, key=lambda box: box.bottom).text))
    opening, ending = amounts
    incoming, outgoing = totals
    if days[0] >= days[1] or opening + incoming - outgoing != ending:
        _fail()
    return days[0], days[1], opening, ending, incoming, outgoing


@dataclass
class NativeMonthlyLedger:
    rows: list[_ParsedTransaction]
    source_pages: tuple[str, ...]
    opening: Decimal
    closing: Decimal


def parse_native_itau_monthly(raw: bytes, page_texts: list[str]) -> NativeMonthlyLedger | None:
    normalized = normalize_upper_text(page_texts[0]) if page_texts else ""
    if "CONTA CORRENTE E APLICACOES AUTOMATICAS" not in normalized or not _COUNTER.search(normalized):
        return None
    import pypdfium2 as pdfium

    rows, source_pages, investment_checks = [], [], []
    accounts = set()
    summary = None
    current_day = None
    printed_closing = None
    automatic_totals = None
    finished = False
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for page_index in range(len(document)):
            with closing(document[page_index]) as page, closing(page.get_textpage()) as tp:
                if page.get_rotation():
                    _fail()
                characters = "".join(chr(pdfium.raw.FPDFText_GetUnicode(tp, index)) for index in range(tp.count_chars()))
                source_pages.append(characters)
                accounts.update(re.findall(r"\bAG\s*(\d{3,5})\s*CC\s*([\d-]+)", normalize_upper_text(characters)))
                if len(accounts) > 1:
                    _fail()
                counters = _COUNTER.findall(characters)
                if counters != [(f"{page_index + 1:03}", f"{len(document):03}")]:
                    _fail()
                columns = _columns(tp)
                if finished:
                    continue  # Printed appendices repeat movements; only the original cash ledger is authoritative.
                if columns is None:
                    _fail()
                money = _boxes(tp, characters, _MONEY)
                if summary is None:
                    summary = _summary(tp, money, columns)
                day_col, history, credit, debit, balance = columns
                boundaries = (credit.right + debit.left) / 2, (debit.right + balance.left) / 2
                dates = [box for box in _boxes(tp, characters, _DATE)
                         if day_col.left - 2 <= box.left and box.right < history.left and box.top < day_col.bottom]
                totalizers = _boxes(tp, characters, re.compile(r"totalizador de aplicações automáticas", re.IGNORECASE))
                limits = [box.top for box in totalizers]
                if totalizers:
                    label = max(totalizers, key=lambda box: box.bottom)
                    totals = sorted((box for box in money if label.bottom - 35 < box.bottom < label.bottom),
                                    key=lambda box: box.left)
                    if len(totals) != 2:
                        _fail()
                    automatic_totals = tuple(abs(_money(box.text)) for box in totals)
                end = max(limits) if limits else 0
                values = [box for box in money if box.left >= credit.left - 2 and end < box.bottom < day_col.bottom - 2]
                source_lines = []
                offset = 0
                for number, line in enumerate(characters.splitlines(keepends=True), 1):
                    first_char = next((index for index, char in enumerate(line) if not char.isspace()), None)
                    if first_char is not None:
                        source_lines.append((number, " ".join(line.split()), tp.get_charbox(offset + first_char)[1]))
                    offset += len(line)
                groups = []
                for box in sorted(values, key=lambda box: -box.bottom):
                    if groups and abs(groups[-1][0].bottom - box.bottom) < 2:
                        groups[-1].append(box)
                    else:
                        groups.append([box])
                for cells in groups:
                    if finished:
                        break  # Same-page debit summaries/investment appendices repeat ledger values.
                    anchor = cells[0]
                    description = " ".join(tp.get_text_bounded(history.left - 1, anchor.bottom - 2,
                        credit.left - 2, anchor.top + 2).split())
                    label = normalize_upper_text(description)
                    amounts = {}
                    for box in cells:
                        center = (box.left + box.right) / 2
                        role = "credit" if center < boundaries[0] else "debit" if center < boundaries[1] else "balance"
                        if role in amounts:
                            _fail()
                        amounts[role] = _money(box.text)
                    if label.startswith("SALDO ANTERIOR"):
                        if set(amounts) != {"balance"} or amounts["balance"] != summary[2]:
                            _fail()
                        continue
                    if label.startswith("SALDO APLIC AUT MAIS"):
                        investment_checks.append((len(rows), amounts.get("balance")))
                        continue
                    if label.startswith(("(-) SALDO A LIBERAR", "SALDO FINAL DISPONIVEL", "SALDO FINAL DEVEDOR")):
                        continue
                    if label.startswith("SALDO EM C/C"):
                        printed_closing = amounts.get("balance")
                        continue
                    if label == "SALDO FINAL":
                        if amounts.get("balance") != summary[3]:
                            _fail()
                        finished = True
                        continue
                    if finished or not label or len(set(amounts) & {"credit", "debit"}) != 1:
                        _fail()
                    eligible = [box for box in dates if box.bottom >= anchor.bottom - 2]
                    if eligible:
                        raw_day = min(eligible, key=lambda box: box.bottom).text
                        try:
                            month = int(raw_day.split("/")[1])
                            year = summary[1].year - (month > summary[1].month)
                            current_day = datetime.strptime(raw_day + f"/{year}", "%d/%m/%Y").date()
                        except ValueError as exc:
                            raise PdfFinancialValidationError(_MESSAGE) from exc
                    if current_day is None or not summary[0] < current_day <= summary[1]:
                        _fail()
                    if "credit" in amounts and amounts["credit"] < 0:
                        _fail()
                    amount = amounts["credit"] if "credit" in amounts else -abs(amounts["debit"])
                    source = next((i for i, line, bottom in source_lines
                                   if description in line and abs(bottom - anchor.bottom) < 3), None)
                    width, height = page.get_size()
                    region = SourceRegion(page_index + 1, history.left / width, (height - anchor.top - 2) / height,
                                          (max(box.right for box in cells) - history.left) / width,
                                          (anchor.top - anchor.bottom + 4) / height)
                    rows.append(build_parsed_transaction(date=current_day.isoformat(), description=description,
                        amount=float(amount), running_balance=float(amounts["balance"]) if "balance" in amounts else None,
                        source_page=page_index + 1, source_line=source, has_explicit_amount_sign=True,
                        source_regions=[region], source_line_refs=((page_index + 1, source),) if source else ()))
    if not rows or not finished or printed_closing is None:
        _fail()
    first = next((index for index, row in enumerate(rows) if row.running_balance is not None), None)
    if first is None:
        _fail()
    opening = Decimal(str(rows[first].running_balance)) - sum((Decimal(str(row.transaction.amount)) for row in rows[:first + 1]))
    if automatic_totals is None:
        # Cash-only editions omit the automatic-investment section entirely.
        # Its absence is valid only when no such movement/position is inferred.
        if opening != summary[2] or any(normalize_upper_text(row.transaction.description).startswith(
                ("APL APLIC AUT MAIS", "RES APLIC AUT MAIS")) for row in rows):
            _fail()
        automatic_totals = (Decimal(0), Decimal(0))
    cash, investment = opening, summary[2] - opening
    incoming = outgoing = Decimal(0)
    automatic_in = automatic_out = Decimal(0)
    checks = {}
    for index, value in investment_checks:
        if index in checks and checks[index] != value:
            _fail()
        checks[index] = value
    if 0 in checks and checks[0] != investment:
        _fail()
    for index, row in enumerate(rows, 1):
        amount = Decimal(str(row.transaction.amount))
        cash += amount
        label = normalize_upper_text(row.transaction.description)
        automatic = label.startswith(("APL APLIC AUT MAIS", "RES APLIC AUT MAIS"))
        if automatic:
            investment -= amount
            automatic_in += max(amount, Decimal(0))
            automatic_out += max(-amount, Decimal(0))
        else:
            incoming += max(amount, Decimal(0))
            outgoing += max(-amount, Decimal(0))
        if row.running_balance is not None and Decimal(str(row.running_balance)) != cash:
            _fail()
        if index in checks and checks[index] != investment:
            _fail()
    if (cash != printed_closing or investment + cash != summary[3]
            or (incoming, outgoing) != summary[4:] or (automatic_in, automatic_out) != automatic_totals):
        _fail()
    return NativeMonthlyLedger(rows, tuple(source_pages), opening, printed_closing)
