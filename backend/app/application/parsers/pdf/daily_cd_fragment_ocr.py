"""Headerless dated cash fragments, identified by geometry and daily controls."""
import re
from dataclasses import replace
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "dated_cd_daily_balance_fragment_ocr_v1"
_BALANCE = re.compile(r"SALDO DO DIA[\s=>]*")
_VALUE = re.compile(rf"(?:R\$\s*)?({NUMBER})\s*([CD])")
_DATE = re.compile(rf"^({DATE})(?:\s+(.*))?$")


def _y(line):
    return line.bbox["top"] + line.bbox["height"] / 2


def is_daily_cd_fragment(extraction):
    if not extraction.pages:
        return False
    lines = [line for page in extraction.pages for line in page.lines if getattr(line, "bbox", None)]
    balances = [line for line in lines if _BALANCE.fullmatch(upper(line.text))]
    first = extraction.pages[0].lines
    if len(balances) < 2 or not first or any(not getattr(line, "bbox", None) for line in first):
        return False
    first_balances = [line for line in first if _BALANCE.fullmatch(upper(line.text))]
    dates = [line for line in first if _DATE.fullmatch(line.text)]
    if not first_balances or not dates:
        return False
    initial = min(first_balances, key=_y)
    return (_y(initial) < min(_y(line) for line in dates)
            and not any(_y(line) < _y(initial) - .004 for line in first))


def parse_daily_cd_fragment(extraction):
    if not is_daily_cd_fragment(extraction):
        return None
    labels = [line for page in extraction.pages for line in page.lines
              if getattr(line, "bbox", None) and _BALANCE.fullmatch(upper(line.text))]
    history_left = median(line.bbox["left"] for line in labels)
    amounts = [line for page in extraction.pages for line in page.lines if getattr(line, "bbox", None)
               and line.bbox["left"] > history_left + .15 and _VALUE.fullmatch(upper(line.text))]
    if not amounts:
        fail()
    value_left = min(line.bbox["left"] for line in amounts)
    rows, running, checks, current = [], None, 0, None
    for page in extraction.pages:
        if not page.lines or any(not getattr(line, "bbox", None) for line in page.lines):
            fail("Uma página do trecho não tem coordenadas suficientes para conferir os movimentos.")
        anchors = sorted([line for line in page.lines if _BALANCE.fullmatch(upper(line.text))
                          or (line.bbox["left"] < history_left - .03 and _DATE.fullmatch(line.text))], key=_y)
        if not anchors or any(_y(line) < _y(anchors[0]) - .004 for line in page.lines):
            fail()
        for index, anchor in enumerate(anchors):
            bottom = _y(anchors[index + 1]) - .004 if index + 1 < len(anchors) else 1
            cells = sorted([line for line in page.lines if _y(anchor) - .004 <= _y(line) < bottom],
                           key=lambda line: (_y(line), line.bbox["left"]))
            financial = [line for line in cells if line.bbox["left"] >= value_left - .025]
            if len(financial) != 1 or not (match := _VALUE.fullmatch(upper(financial[0].text))):
                fail("Não foi possível associar um valor com sinal C/D a cada linha financeira do trecho.")
            amount = money(match[1]) * (-1 if match[2] == "D" else 1)
            if _BALANCE.fullmatch(upper(anchor.text)):
                allowed = {anchor.id, financial[0].id}
                if any(line.id not in allowed and not re.fullmatch(r"[\s=>]+", line.text) for line in cells):
                    fail()
                if running is not None:
                    if not rows:
                        fail()
                    checks += 1
                    if running != amount:
                        financial_mismatch()
                    control = _source(replace(rows[-1], running_balance=float(amount)), page, cells)
                    rows[-1] = replace(rows[-1], running_balance=control.running_balance,
                        source_regions=(*rows[-1].source_regions, *control.source_regions),
                        source_line_refs=(*rows[-1].source_line_refs, *control.source_line_refs))
                running = amount
                continue
            date_match = _DATE.fullmatch(anchor.text)
            date = day(date_match[1])
            if running is None or (current and date < current):
                fail()
            current = date
            history = [line for line in cells if history_left - .025 <= line.bbox["left"] < value_left - .04]
            if not history:
                fail()
            document = [date_match[2]] if date_match[2] else []
            document += [line.text for line in cells if line.id != anchor.id and line.bbox["left"] < history_left - .025]
            if any(re.search(r"\d{2}/\d{2}", text) for text in document):
                fail()
            rows.append(_source(_row(page.page_number, anchor.line_index, date,
                " ".join(line.text for line in history), amount, reference=" ".join(document) or None), page, cells))
            running += amount
    if not rows or checks < 1:
        fail()
    return VerifiedStatement(rows, LAYOUT, None, None, partial=True, candidate_rows=len(rows),
                             printed_balance_checks=checks, balance_bounds_unavailable=True)
