"""Banestes split day/month groups with cash/Rende+ and projected-yield controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "banestes_conta_rende_split_date_v1"
MONTHS = "JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ".split()
CASH = re.compile(rf"(?:R\$\s*)?(-?)\s*({NUMBER})")


def _value(line):
    match = CASH.fullmatch(line.text.strip())
    if match is None:
        fail("Um valor Banestes não está legível.")
    return money(match[2]) * (-1 if match[1] else 1)


def parse_banestes_rende(source):
    all_lines = [line for page in source.pages for line in page.lines]
    text = "\n".join(upper(line.text) for line in all_lines)
    periods = re.findall(rf"PERIODO:\s*({DATE})\s+[AÀ]\s+({DATE})", text)
    if len(periods) != 1:
        fail()
    start, end = map(day, periods[0])
    if start > end:
        fail()
    rows, running, opening, closing, checks = [], None, None, None, 0
    inflows = outflows = Decimal(0)
    for page in source.pages:
        lines = page.lines
        beginnings = [line for line in lines if upper(line.text) == "SALDO ANTERIOR"]
        footers = [line for line in lines if upper(line.text) == "SALDOS"]
        if len(beginnings) != 1 or len(footers) != 1 or opening is not None:
            fail("Não foi possível conferir todas as páginas do extrato Banestes.")
        first, footer = beginnings[0], footers[0]
        def amount_at(label):
            values = [line for line in lines if line.bbox["left"] >= .82
                      and abs(line.bbox["top"] - label.bbox["top"]) < .006 and CASH.fullmatch(line.text.strip())]
            if len(values) != 1:
                fail()
            return values[0]
        opening = running = _value(amount_at(first))
        body = [line for line in lines if first.bbox["top"] + .006 < line.bbox["top"] < footer.bbox["top"]]
        balances = [line for line in body if upper(line.text) == "SALDO CONTA/RENDE+"]
        cursor = first.bbox["top"] + .006
        for boundary in [*balances, footer]:
            chunk = [line for line in body if cursor <= line.bbox["top"] < boundary.bbox["top"] - .004]
            months = [line for line in body if cursor <= line.bbox["top"] <= boundary.bbox["top"] + .006
                      and line.bbox["left"] < .12 and re.fullmatch(r"[A-Z]{3}/\d{2}", upper(line.text))]
            days = [line for line in chunk if line.bbox["left"] < .12 and re.fullmatch(r"\d{1,2}", line.text)]
            if len(months) != 1 or len(days) != 1:
                fail("Não foi possível associar o dia e o mês de cada grupo Banestes.")
            month, year = upper(months[0].text).split("/")
            if month not in MONTHS:
                fail()
            date = day(f"{int(days[0].text):02d}/{MONTHS.index(month)+1:02d}/20{year}")
            if not start <= date <= end:
                fail()
            values = sorted([line for line in chunk if line.bbox["left"] >= .82 and CASH.fullmatch(line.text.strip())],
                            key=lambda line: line.bbox["top"])
            if not values:
                fail()
            low = cursor
            for value in values:
                cells = [line for line in chunk if low <= line.bbox["top"] <= value.bbox["top"] + .006
                         and .12 <= line.bbox["left"] < .82]
                description = " ".join(line.text for line in cells)
                if not description:
                    fail()
                amount = _value(value)
                running += amount
                inflows += max(amount, Decimal(0))
                outflows += max(-amount, Decimal(0))
                row = _source(_row(page.page_number, value.line_index, date, description, amount, running),
                              page, [*days, *months, *cells, value])
                rows.append(row)
                low = value.bbox["top"] + .006
            if boundary is not footer:
                balance_line = amount_at(boundary)
                if _value(balance_line) != running:
                    fail("O saldo diário Banestes não corresponde às movimentações impressas.")
                rows[-1] = replace(rows[-1], source_line_refs=(*rows[-1].source_line_refs,
                    (page.page_number, boundary.line_index), (page.page_number, balance_line.line_index)))
                checks += 1
            cursor = boundary.bbox["top"] + .006
        controls = {}
        for label in ("SALDO CONTA", "SALDO RENDE+", "RENDIMENTO PREVISTO RENDE+", "SALDO TOTAL"):
            labels = [line for line in lines if upper(line.text) == label and line.bbox["top"] > footer.bbox["top"]]
            if len(labels) != 1:
                fail()
            controls[label] = _value(amount_at(labels[0]))
        closing = controls["SALDO CONTA"] + controls["SALDO RENDE+"]
        header_values = [_value(line) for line in lines if .5 <= line.bbox["left"] < .82
                         and line.bbox["top"] < first.bbox["top"] and CASH.fullmatch(line.text.strip())]
        headline = [_value(line) for line in lines if .3 <= line.bbox["left"] < .5
                    and line.bbox["top"] < first.bbox["top"] and CASH.fullmatch(line.text.strip())]
        total = controls["SALDO TOTAL"]
        if (running != closing or total != closing + controls["RENDIMENTO PREVISTO RENDE+"]
                or header_values != [inflows, outflows] or headline != [total]):
            fail("Os saldos e totais Banestes não correspondem às movimentações impressas.")
        checks += 4
    if not rows or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    # The printed ledger combines cash and automatic investments. Its controls
    # validate the movements, but must not become a pure-cash OFX balance.
    rows[0] = replace(rows[0], warnings=[*rows[0].warnings, "manual_review_recommended",
                                       "combined_cash_investment_balance_not_comparable"])
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks,
                             candidate_rows=len(rows), balance_bounds_unavailable=True)
