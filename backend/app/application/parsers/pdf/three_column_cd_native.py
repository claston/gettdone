"""Dated three-column ledgers with explicit C/D signs and printed balances."""
import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "dated_cd_three_column_statement_v1"
_VALUE = re.compile(rf"({NUMBER})\s*([CD])")


def parse_three_column_cd(source):
    columns = _headers(source.pages[0], ("DIA", "HISTORICO", "VALOR"))
    if not columns or not columns["DIA"] < columns["HISTORICO"] < columns["VALOR"]:
        fail()
    rows, opening, running, last_balance, current, checks = [], None, None, None, None, 0
    last_control = False
    for page in source.pages:
        if not page.lines or any(not line.bbox for line in page.lines):
            fail()
        header = max((line.bbox["top"] for line in page.lines if upper(line.text) in columns), default=0)
        lines = [line for line in page.lines if line.bbox["top"] > header + .005]
        dates = sorted([line for line in lines if line.bbox["left"] < columns["HISTORICO"] - .03
                        and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"])
        cash = [line for line in lines if line.bbox["left"] >= columns["VALOR"] - .03]
        used = set()
        if not dates:
            fail()
        for index, anchor in enumerate(dates):
            y = anchor.bbox["top"]
            bottom = dates[index+1].bbox["top"] - .005 if index+1 < len(dates) else y + .025
            cells = sorted([line for line in lines if y - .005 <= line.bbox["top"] < bottom],
                           key=lambda line: (line.bbox["top"], line.bbox["left"]))
            values = [line for line in cash if line in cells]
            history = [line for line in cells if columns["HISTORICO"] - .02 <= line.bbox["left"] < columns["VALOR"] - .04
                       and line.bbox["height"] <= anchor.bbox["height"] * 2]
            if len(values) != 1 or not history or not (match := _VALUE.fullmatch(upper(values[0].text))):
                fail()
            used.add(values[0].id)
            date = day(anchor.text)
            if current and date < current:
                fail()
            current = date
            amount = money(match[1]) * (-1 if match[2] == "D" else 1)
            label = " ".join(line.text for line in history)
            compact = re.sub(r"\s+", "", upper(label))
            if compact == "SALDOANTERIOR":
                if opening is not None or rows:
                    fail()
                opening = running = amount
                last_control = False
            elif compact in {"SALDO", "SALDODIA", "SALDODODIA", "SALDOATUAL"}:
                if not rows:
                    fail()
                if amount != running:
                    financial_mismatch()
                controlled = _source(rows[-1], page, cells)
                rows[-1] = replace(rows[-1], running_balance=float(amount),
                    source_regions=(*rows[-1].source_regions, *controlled.source_regions),
                    source_line_refs=(*rows[-1].source_line_refs, *controlled.source_line_refs))
                last_balance, last_control = amount, True
                checks += 1
            else:
                if running is None:
                    fail()
                rows.append(_source(_row(page.page_number, anchor.line_index, date, label, amount), page, cells))
                running += amount
                last_control = False
        if used != {line.id for line in cash}:
            fail()
    if not rows or not last_control:
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, last_balance, printed_balance_checks=checks)
