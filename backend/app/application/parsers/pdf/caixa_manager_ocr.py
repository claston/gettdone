"""Caixa Gerenciador posting/effective-date ledger, including quarter-turned OCR."""
import re
from dataclasses import replace
from datetime import date as calendar_date
from datetime import timedelta

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.ocr_statement_controls import _provenance
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "caixa_gerenciador_periodo_financial_controls_ocr_v1"
_MONEY = re.compile(rf"(?:[+-]\s*)?R\$\s*([+-]?{NUMBER})\s*([CD(\[]|(?<=\s)[0O])?$", re.I)


def _explicit_sign(text, match):
    signs = []
    before = re.match(r"\s*([+-])", text)
    if before:
        signs.append(-1 if before[1] == "-" else 1)
    if match[1].startswith(("+", "-")):
        signs.append(-1 if match[1][0] == "-" else 1)
    if len(signs) > 1:
        fail("O valor Caixa contém sinais duplicados.")
    if (match[2] or "").upper() in {"C", "D"}:
        signs.append(-1 if match[2].upper() == "D" else 1)
    if len(set(signs)) > 1:
        fail("O valor Caixa contém sinais conflitantes.")
    return signs[0] if signs else None


def is_caixa_manager(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    return all(token in text for token in ("GERENCIADOR", "CAIXA", "EXTRATO NO PERIODO DE", "DATA EFETIVA"))


def parse_caixa_manager(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    period = re.search(rf"EXTRATO NO PERIODO DE\s+({DATE})\s+A\s+({DATE})", text)
    start, end = optional_document_period(period)
    dates = [line for page in extraction.pages for line in page.lines if re.fullmatch(DATE, line.text) and line.bbox]
    if not dates:
        fail()
    turned = sum(line.bbox["height"] > line.bbox["width"]*2 for line in dates) > len(dates)/2

    slope = 0
    def position(line):
        box = line.bbox
        x, y = (1-box["top"]-box["height"], box["left"]) if turned else (box["left"], box["top"])
        return x, y - slope*x

    rows, opening, running = [], None, None
    checks = 0
    scopes, checkpoints = [], {}
    for page in extraction.pages:
        slope = 0
        page_text = upper("\n".join(line.text for line in page.lines))
        page_period = re.search(rf"EXTRATO NO PERIODO DE\s+({DATE})\s+A\s+({DATE})", page_text)
        if page_period:
            scope = tuple(map(day, page_period.groups()))
            if scope[0] > scope[1] or scopes and scope != scopes[-1] and scope[0] <= scopes[-1][1]:
                fail("Os períodos do extrato Caixa estão sobrepostos ou fora de sequência.")
            if not scopes or scopes[-1] != scope:
                scopes.append(scope)
            start, end = scope
        lines = [line for line in page.lines if line.bbox]
        repeated_signs = set()
        prepared = []
        for line in lines:
            duplicated = re.fullmatch(r"([+-])\s+\1\s+(R\$\s*.+)", line.text.strip())
            if duplicated:
                repeated_signs.add(line.id)
                line = replace(line, text=f"{duplicated[1]} {duplicated[2]}")
            prepared.append(line)
        lines = prepared
        labels = {upper(line.text): line for line in lines}
        has_table = all(label in labels for label in ("VALOR", "SALDO", "DATA EFETIVA"))
        values = [line for line in lines if _MONEY.fullmatch(line.text.strip())]
        if not has_table:
            if values:
                fail()
            continue
        x1, y1 = position(labels["VALOR"])
        x2, y2 = position(labels["SALDO"])
        if x2 <= x1 or abs((y2-y1)/(x2-x1)) > .12:
            fail("A inclinação da tabela Caixa impede conferir as colunas.")
        slope = (y2-y1)/(x2-x1)
        table_top = max(position(labels[key])[1] for key in ("VALOR", "SALDO", "DATA EFETIVA"))
        boundary = (position(labels["VALOR"])[0]+position(labels["SALDO"])[0])/2
        used = set()
        opening_labels = [line for line in lines if upper(line.text).startswith("SALDO ANTERIOR AO PERIODO")]
        if opening_labels:
            if len(opening_labels) != 1 or len(scopes) == 1 and opening is not None:
                fail()
            seeds = [line for line in values if abs(position(line)[1]-position(opening_labels[0])[1]) < .02]
            if len(seeds) != 1:
                fail()
            match = _MONEY.fullmatch(seeds[0].text.strip())
            if not match[2]:
                fail()
            running = abs(money(match[1])) * _explicit_sign(seeds[0].text, match)
            if opening is None:
                opening = running
            checkpoints[len(rows)] = float(running)
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
            recovered_indicator = (balance_match[2] or "").upper() not in {"C", "D"}
            if recovered_indicator:
                magnitude = abs(money(balance_match[1]))
                if len(amounts) == 1:
                    parsed_amount = _MONEY.fullmatch(amounts[0].text.strip())
                    amount_magnitude = abs(money(parsed_amount[1]))
                    explicit_amount = _explicit_sign(amounts[0].text, parsed_amount)
                    directions = (explicit_amount,) if explicit_amount is not None else (-1, 1)
                    candidates = {candidate for candidate in (-magnitude, magnitude)
                                  if candidate-running in {amount_magnitude*sign for sign in directions}}
                else:
                    candidates = {running} if abs(running) == magnitude else set()
                if len(candidates) != 1:
                    fail("O indicador de saldo Caixa está cortado e não pode ser confirmado pelos demais valores.")
                balance = candidates.pop()
            else:
                if (balance_match[2] or "").upper() not in {"C", "D"}:
                    fail()
                balance = abs(money(balance_match[1])) * _explicit_sign(balances[0].text, balance_match)
            date = day(anchor.text)
            next_week = (calendar_date.fromisoformat(end) + timedelta(days=7)).isoformat() if end else None
            if not period_contains(date, start, next_week) or rows and date < rows[-1].transaction.date:
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
            magnitude = abs(money(parsed[1]))
            amount = balance-running
            explicit = _explicit_sign(amounts[0].text, parsed)
            if abs(amount) != magnitude or explicit is not None and amount != magnitude * explicit:
                fail()
            description = " ".join(line.text for line in sorted(cells, key=lambda item: item.line_index)
                                   if .23 < position(line)[0] < position(labels["VALOR"])[0]-.04 and line not in cash)
            if not description:
                fail()
            row = _provenance(_row(page.page_number, anchor.line_index, date, description, amount, balance), page, cells)
            if recovered_indicator:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended",
                                             "balance_indicator_from_independent_controls"])
            if end is not None and date > end:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "movement_date_outside_requested_period"])
            if amounts[0].id in repeated_signs:
                row = replace(row, warnings=[*row.warnings, "ocr_duplicate_sign_normalized"])
            rows.append(row)
            running = balance
            checks += 1
        if used != {line.id for line in values}:
            fail()
    if not rows or checks < len(rows) or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, running, partial=len(scopes)>1,
                             printed_balance_checks=checks, balance_checkpoints=checkpoints)
