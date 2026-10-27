"""Unbranded descending web cards, with printed dates and daily controls."""
import re
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money

_MONTHS = {name: index for index, name in enumerate(("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"), 1)}
_CASH = re.compile(rf"([+-]?)\s*R\$\s*({NUMBER})")
_DATE = re.compile(r"(\d{2}) DE ([A-Z]+) DE (\d{4}), .+")


def _cash(line):
    match = _CASH.fullmatch(line.text)
    if not match:
        fail("Um valor do extrato por período está ilegível.")
    return money(match[2]) * (-1 if match[1] == "-" else 1)


def _split_tags(page):
    lines = []
    for line in page.lines:
        cells = [word for word in page.words if word.id in line.word_ids]
        groups = ([word for word in cells if word.bbox["left"] < .78],
                  [word for word in cells if word.bbox["left"] >= .78])
        if line.bbox["left"] < .6 and all(groups) and re.fullmatch(r"\d{2}[A-Z]{3}", upper(" ".join(w.text for w in groups[1]))):
            for index, group in enumerate(groups):
                left = min(w.bbox["left"] for w in group)
                right = max(w.bbox["left"] + w.bbox["width"] for w in group)
                lines.append(replace(line, id=f"{line.id}:{index}", text=" ".join(w.text for w in group),
                    bbox={**line.bbox, "left": left, "width": right-left}, word_ids=tuple(w.id for w in group)))
        else:
            lines.append(line)
    return replace(page, lines=lines)


def parse_descending_web_cards(source):
    if len(source.pages) != 1:
        fail("Envie a página completa do extrato por período, com o saldo anterior.")
    page = _split_tags(source.pages[0])
    height = median(line.bbox["height"] for line in page.lines)
    tolerance = height * .8
    cash = [line for line in page.lines if line.bbox["left"] > .6 and "R$" in line.text]
    for line in cash:
        _cash(line)
    used = set()

    def near(label):
        matches = [line for line in cash if abs(line.bbox["top"] - label.bbox["top"]) < tolerance]
        if len(matches) != 1:
            fail("Um saldo diário não tem seu valor inequívoco.")
        used.add(matches[0].id)
        return matches[0]

    openings = [line for line in page.lines if upper(line.text) == "SALDO ANTERIOR"]
    if len(openings) != 1:
        fail("O extrato por período está sem seu saldo anterior.")
    opening_label = openings[0]
    opening = _cash(near(opening_label))
    dates = sorted((line for line in page.lines if _DATE.fullmatch(upper(line.text))), key=lambda line: line.bbox["top"])
    if not dates:
        fail()
    rows, days = [], []
    for index, header in enumerate(dates):
        match = _DATE.fullmatch(upper(header.text))
        month = _MONTHS.get(match[2])
        if month is None:
            fail()
        date = day(f"{match[1]}/{month:02}/{match[3]}")
        tag = match[1] + match[2][:3]
        stop = dates[index+1].bbox["top"] if index+1 < len(dates) else opening_label.bbox["top"]
        cells = [line for line in page.lines if header.bbox["top"] < line.bbox["top"] < stop]
        balances = [line for line in cells if upper(line.text) == "SALDO DO DIA"]
        if len(balances) != 1:
            fail("Um dia do extrato está sem seu saldo impresso.")
        balance_label = balances[0]
        balance_cell = near(balance_label)
        amounts = sorted((line for line in cash if header.bbox["top"] < line.bbox["top"] < balance_label.bbox["top"]-tolerance),
            key=lambda line: line.bbox["top"])
        if not amounts:
            fail()
        total = Decimal(0)
        for number, anchor in enumerate(amounts):
            high = amounts[number+1].bbox["top"] if number+1 < len(amounts) else balance_label.bbox["top"]
            band = [line for line in cells if anchor.bbox["top"]-height*.4 <= line.bbox["top"] < high-height*.4]
            tags = [line for line in band if line.bbox["left"] >= .78 and re.fullmatch(r"\d{2}[A-Z]{3}", upper(line.text))]
            history = [line for line in band if .15 <= line.bbox["left"] < .6]
            if len(tags) != 1 or upper(tags[0].text) != tag or not history:
                fail("Um movimento está sem data ou descrição associada no extrato por período.")
            value = _cash(anchor)
            total += value
            used.add(anchor.id)
            rows.append(_source(_row(page.page_number, anchor.line_index, date,
                " ".join(line.text for line in history), value, None), page, [header, *band]))
        days.append((date, total, _cash(balance_cell)))
    if used != {line.id for line in cash}:
        fail("Há um valor sem movimento ou saldo associado no extrato por período.")
    if any(a[0] <= b[0] for a, b in zip(days, days[1:])):
        fail("A ordem dos dias do extrato por período não está confirmada.")
    running = opening
    for _, total, printed in reversed(days):
        if running + total != printed:
            financial_mismatch()
        running = printed
    return VerifiedStatement(rows, "descending_web_cards_daily_cash_v1", opening, running,
        printed_balance_checks=len(days), candidate_rows=len(rows), balance_checkpoints={})
