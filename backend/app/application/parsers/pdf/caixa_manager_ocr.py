"""Caixa Gerenciador posting/effective-date ledger, including quarter-turned OCR."""
import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.ocr_statement_controls import _provenance
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "caixa_gerenciador_periodo_financial_controls_ocr_v1"
_MONEY = re.compile(rf"R\$\s*({NUMBER})\s*([CD]?)$", re.I)


def is_caixa_manager(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    return all(token in text for token in ("GERENCIADOR", "CAIXA", "EXTRATO NO PERIODO DE", "DATA EFETIVA"))


def parse_caixa_manager(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    period = re.search(rf"EXTRATO NO PERIODO DE\s+({DATE})\s+A\s+({DATE})", text)
    if period is None:
        fail()
    start, end = day(period[1]), day(period[2])
    dates = [line for page in extraction.pages for line in page.lines if re.fullmatch(DATE, line.text) and line.bbox]
    if not dates:
        fail()
    turned = sum(line.bbox["height"] > line.bbox["width"]*2 for line in dates) > len(dates)/2

    def position(line):
        box = line.bbox
        return (1-box["top"]-box["height"], box["left"]) if turned else (box["left"], box["top"])

    rows, opening, running = [], None, None
    checks = 0
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        labels = {upper(line.text): line for line in lines}
        has_table = all(label in labels for label in ("VALOR", "SALDO", "DATA EFETIVA"))
        values = [line for line in lines if _MONEY.fullmatch(line.text.strip())]
        if not has_table:
            if values:
                fail()
            continue
        table_top = max(position(labels[key])[1] for key in ("VALOR", "SALDO", "DATA EFETIVA"))
        boundary = (position(labels["VALOR"])[0]+position(labels["SALDO"])[0])/2
        used = set()
        opening_labels = [line for line in lines if upper(line.text).startswith("SALDO ANTERIOR AO PERIODO")]
        if opening_labels:
            if opening is not None or len(opening_labels) != 1:
                fail()
            seeds = [line for line in values if abs(position(line)[1]-position(opening_labels[0])[1]) < .02]
            if len(seeds) != 1:
                fail()
            match = _MONEY.fullmatch(seeds[0].text.strip())
            if not match[2]:
                fail()
            opening = running = money(match[1])*(-1 if match[2].upper() == "D" else 1)
            used.add(seeds[0].id)
        anchors = sorted([line for line in lines if position(line)[0] < .16
                          and position(line)[1] > table_top and re.fullmatch(DATE, line.text)], key=lambda item: position(item)[1])
        for index, anchor in enumerate(anchors):
            y = position(anchor)[1]
            low = (position(anchors[index-1])[1]+y)/2 if index else table_top
            high = (position(anchors[index+1])[1]+y)/2 if index+1 < len(anchors) else 1
            cells = [line for line in lines if low < position(line)[1] <= high]
            cash = [line for line in cells if line in values]
            balances = [line for line in cash if position(line)[0] > boundary]
            amounts = [line for line in cash if position(line)[0] <= boundary]
            if len(balances) != 1 or running is None:
                fail()
            balance_match = _MONEY.fullmatch(balances[0].text.strip())
            if not balance_match[2]:
                fail()
            balance = money(balance_match[1])*(-1 if balance_match[2].upper() == "D" else 1)
            date = day(anchor.text)
            if not start <= date <= end or rows and date < rows[-1].transaction.date:
                fail()
            daily = any(upper(line.text) == "SALDO DIA" for line in cells)
            used.update(line.id for line in cash)
            if daily:
                if amounts or balance != running or not rows or date != rows[-1].transaction.date:
                    fail()
                rows[-1] = replace(rows[-1], running_balance=float(balance),
                                   source_line_refs=(*rows[-1].source_line_refs, (page.page_number, anchor.line_index)))
                checks += 1
                continue
            if len(amounts) != 1:
                fail()
            parsed = _MONEY.fullmatch(amounts[0].text.strip())
            magnitude = money(parsed[1])
            amount = balance-running
            if abs(amount) != magnitude or parsed[2] and (amount < 0) != (parsed[2].upper() == "D"):
                fail()
            description = " ".join(line.text for line in sorted(cells, key=lambda item: item.line_index)
                                   if .23 < position(line)[0] < position(labels["VALOR"])[0]-.04 and line not in cash)
            if not description:
                fail()
            rows.append(_provenance(_row(page.page_number, anchor.line_index, date, description, amount, balance), page, cells))
            running = balance
            checks += 1
        if used != {line.id for line in values}:
            fail()
    if not rows or checks < len(rows) or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, running, printed_balance_checks=checks)
