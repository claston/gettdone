"""Banpará's printed amount and daily-balance columns; no sign inference."""

import re
from decimal import Decimal

from app.application.errors import OcrFinancialAmbiguityError
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "banco_banpara_extrato_lancamentos_v1"
MONEY = re.compile(rf"[+-]?{NUMBER}")
MESSAGE = "Não foi possível separar as colunas de valor e saldo do extrato Banpará. Confira o documento original."


def parse_banpara_ocr(extraction):
    text = upper(" ".join(line.text for page in extraction.pages for line in page.lines))
    if "BANPARA" not in text and "BANCO DO ESTADO DO PARA" not in text:
        return None
    period = re.search(rf"PERIODO:\s*({DATE})\s+A\s+({DATE})", text)
    if not period:
        fail(MESSAGE)
    start, end = day(period[1]), day(period[2])
    rows, opening, running, closing, checks = [], None, None, None, 0
    base = _headers(extraction.pages[0], ("DOC.", "VALOR", "SALDO"))
    if not base:
        fail(MESSAGE)
    for page in extraction.pages:
        lines = sorted((line for line in page.lines if line.bbox), key=lambda line: (line.bbox["top"], line.bbox["left"]))
        columns = _headers(page, ("DOC.", "VALOR", "SALDO")) or base
        split = (columns["VALOR"] + columns["SALDO"]) / 2
        amount_left = columns["DOC."] + .06
        used_amounts = set()

        def value(cells, left, right):
            candidates = [line for line in cells if left <= line.bbox["left"] < right and MONEY.fullmatch(line.text.strip())]
            if len(candidates) > 1:
                raise OcrFinancialAmbiguityError(MESSAGE)
            if not candidates:
                return None
            line = candidates[0]
            if any(word.id in line.word_ids and word.text_type == "HANDWRITING" for word in page.words):
                raise OcrFinancialAmbiguityError(MESSAGE)
            if left == amount_left:
                used_amounts.add(line.id)
            return money(line.text.strip())

        for label in lines:
            if upper(label.text) not in {"SALDO ANTERIOR", "SALDO ATUAL", "SALDO FINAL"}:
                continue
            cells = [line for line in lines if abs(line.bbox["top"] - label.bbox["top"]) < .006]
            control = value(cells, split, 1)
            if control is None:
                fail(MESSAGE)
            if upper(label.text) == "SALDO ANTERIOR":
                if opening is None:
                    opening = running = control
                elif rows and running != control:
                    financial_mismatch()
            else:
                closing = control
        anchors = [line for line in lines if line.bbox["left"] < columns["DOC."] - .05
                   and re.match(r"^\d{2}/\d{2}(?:\s|$)", line.text.strip())]
        for index, anchor in enumerate(anchors):
            match = re.fullmatch(r"(\d{2}/\d{2})(?:\s+(.+))?", anchor.text.strip())
            date = day(match[1] + "/" + start[:4])
            if not start <= date <= end or rows and date < rows[-1].transaction.date:
                fail(MESSAGE)
            y = anchor.bbox["top"]
            bottom = anchors[index + 1].bbox["top"] - .003 if index + 1 < len(anchors) else min(y + .065, 1)
            cells = [line for line in lines if y - .004 <= line.bbox["top"] < bottom]
            baseline = [line for line in cells if abs(line.bbox["top"] - y) < .006]
            amount = value(baseline, amount_left, split)
            balance = value(baseline, split, 1)
            if amount is None or running is None:
                raise OcrFinancialAmbiguityError(MESSAGE)
            running += amount
            if balance is not None:
                if running != balance:
                    financial_mismatch()
                checks += 1
            reference = [line.text.strip() for line in baseline if columns["DOC."] - .03 <= line.bbox["left"] < amount_left
                         and re.fullmatch(r"\d+", line.text.strip())]
            if len(reference) > 1:
                raise OcrFinancialAmbiguityError(MESSAGE)
            details = [line.text.strip() for line in cells if line is not anchor and line.bbox["left"] < columns["DOC."] - .05]
            description = " ".join([*([match[2]] if match[2] else []), *details])
            if not description:
                raise OcrFinancialAmbiguityError(MESSAGE)
            rows.append(_source(_row(page.page_number, anchor.line_index, date, description, amount, balance,
                                     reference[0] if reference else None), page, cells))
        header_y = max((line.bbox["top"] for line in lines if upper(line.text) in {"DOC.", "VALOR", "SALDO"}), default=0)
        if any(line.bbox["top"] > header_y and amount_left <= line.bbox["left"] < split
               and MONEY.fullmatch(line.text.strip()) and line.id not in used_amounts for line in lines):
            raise OcrFinancialAmbiguityError(MESSAGE)
    if not rows:
        fail(MESSAGE)
    if closing is not None and closing != running:
        financial_mismatch()
    if closing is None:
        closing = Decimal(str(rows[-1].running_balance)) if rows[-1].running_balance is not None else None
        if closing is None:
            financial_mismatch()
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks)
