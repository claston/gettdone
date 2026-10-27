"""Uniprime cash columns with a printed running balance on every movement."""

import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "uniprime_conta_corrente_ocr_v1"
_VALUE = re.compile(rf"({NUMBER})\s*([+-])?")
_CLIPPED = re.compile(r"([+-]?)(\d[\d.]*,\d{1,2})(?:\.{3}|…)")


def is_uniprime_statement(text):
    normalized = upper(text)
    return all(marker in normalized for marker in ("UNIPRIME", "EXTRATO DE", "SALDO ANTERIOR", "HISTORICO", "VALOR (R$)", "SALDO (R$)"))


def _one(cells, left, right, *, clipped=False, by_center=False):
    matches = [line for line in cells if left <= line.bbox["left"] + (line.bbox["width"] / 2 if by_center else 0) < right
               and (_VALUE.fullmatch(line.text.strip()) or clipped and _CLIPPED.fullmatch(line.text.strip()))]
    if len(matches) != 1:
        fail("Não foi possível ler todas as colunas de valor e saldo Uniprime. Envie uma cópia mais nítida.")
    return matches[0]


def _value(line):
    match = _VALUE.fullmatch(line.text.strip())
    return money(match[1]) * (-1 if match[2] == "-" else 1), match[2]


def parse_uniprime(extraction):
    text = "\n".join(line.text for page in extraction.pages for line in page.lines)
    if not is_uniprime_statement(text):
        return None
    periods = set(re.findall(rf"EXTRATO DE\s+({DATE})\s+A\s+({DATE})", upper(text)))
    columns = _headers(extraction.pages[0], ("DATA", "HISTORICO", "VALOR", "SALDO"))
    if not columns or len(periods) != 1:
        fail()
    period = periods.pop()
    start, end = day(period[0]), day(period[1])
    if start > end:
        fail()
    opening = running = None
    rows, checks = [], 0
    for page in extraction.pages:
        native = extraction.provider == "native_pdfium_text"
        lines = [line for line in page.lines if line.bbox]
        for label in [line for line in lines if upper(line.text).startswith("SALDO ANTERIOR")]:
            if opening is not None or rows:
                fail()
            cells = [line for line in lines if abs(line.bbox["top"] - label.bbox["top"]) < .006]
            opening = running = _value(_one(cells, columns["SALDO"] - (.015 if native else 0), 1, by_center=native))[0]
        anchors = sorted([line for line in lines if line.bbox["left"] < columns["HISTORICO"] - .1
                          and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"])
        if extraction.provider == "native_pdfium_text":
            header_y = max((line.bbox["top"] for line in lines if upper(line.text) == "DATA"), default=0)
            anchors = [line for line in anchors if line.bbox["top"] > header_y]
        # Every monetary cell in the movement column needs one readable date.
        # This also catches date-less rows whose net effect could cancel out.
        cash_cells = [line for line in lines
                      if columns["VALOR"] - .015 <= line.bbox["left"] + (line.bbox["width"] / 2 if native else 0)
                      < columns["SALDO"] - .015
                      and re.search(r"\d[,\.]\d", line.text)]
        for cell in cash_cells:
            if len([anchor for anchor in anchors if abs(anchor.bbox["top"] - cell.bbox["top"]) < .006]) != 1:
                fail("Há um lançamento Uniprime sem data legível. Envie o documento completo e nítido.")
        for index, anchor in enumerate(anchors):
            date = day(anchor.text)
            top = anchor.bbox["top"]
            bottom = anchors[index+1].bbox["top"] - .005 if index+1 < len(anchors) else top + .023
            cells = [line for line in lines if top - .005 <= line.bbox["top"] < bottom]
            baseline = [line for line in cells if abs(line.bbox["top"]-top) < .006]
            if running is None or not start <= date <= end:
                fail()
            amount_cell = _one(baseline, columns["VALOR"] - .015, columns["SALDO"] - .015,
                               clipped=native, by_center=native)
            balance_cell = _one(baseline, columns["SALDO"] - .015, 1, by_center=native)
            balance, _ = _value(balance_cell)
            warnings = []
            truncated = _CLIPPED.fullmatch(amount_cell.text.strip())
            if truncated:
                amount, sign = balance - running, None
                observed = truncated[2].replace(".", "")
                if (not amount or not format(abs(amount), ".2f").replace(".", ",").startswith(observed)
                        or truncated[1] and (amount < 0) != (truncated[1] == "-")):
                    fail("O valor truncado Uniprime não confere com os dígitos visíveis e os dois saldos impressos.")
                warnings = ["manual_review_recommended", "native_amount_recovered_from_balances"]
            else:
                amount, sign = _value(amount_cell)
            if sign is None:
                # A missing OCR sign can only be recovered from two adjacent
                # printed balances and the independently read exact magnitude.
                if not truncated and (abs(balance - running) != amount or amount == 0):
                    fail()
                amount = balance - running
                warnings = [*warnings, "manual_review_recommended", "amount_sign_from_printed_balances"]
            if running + amount != balance:
                financial_mismatch()
            history = [line for line in cells if columns["HISTORICO"] - .015 <= line.bbox["left"] < columns["VALOR"] - .025]
            if not history:
                fail()
            refs = [line for line in baseline if columns["DATA"] + .09 <= line.bbox["left"] < columns["HISTORICO"] - .025]
            if len(refs) > 1:
                fail()
            row = _source(_row(page.page_number, anchor.line_index, date, " ".join(line.text for line in history),
                               amount, balance, refs[0].text if refs else None), page, cells)
            rows.append(replace(row, has_explicit_amount_sign=sign is not None, warnings=[*row.warnings, *warnings]))
            running = balance
            checks += 1
    if not rows or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, running, printed_balance_checks=checks)
