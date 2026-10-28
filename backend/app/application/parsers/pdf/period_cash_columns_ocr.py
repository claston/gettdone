"""Known Caixa/Nordeste cash tables with independent printed row balances."""
import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import (
    optional_document_period,
    period_contains,
)
from app.application.parsers.pdf.ocr_statement_controls import _provenance
from app.application.parsers.pdf.row_geometry import local_row_slopes, nearest_row, row_slope
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_CASH = re.compile(rf"(?:R\$\s*)?((?:[+-]\s*){{0,2}}{NUMBER})\s*([CD€]?)", re.I)
_MONTHS = "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split()


def _value(line, *, require_sign=False, cells=()):
    match = _CASH.fullmatch(line.text.strip())
    if match is None:
        fail()
    token = re.sub(r"\s", "", match[1])
    signs = re.match(r"^[+-]+", token)
    if signs and len(signs[0]) > 1:
        if require_sign or len(set(signs[0])) != 1:
            fail("O valor contém sinais conflitantes ou duplicados.")
        token = signs[0][0] + token[len(signs[0]):]
    value = money(token)
    indicator = match[2].upper()
    if not indicator:
        signs = [cell.text.upper() for cell in cells if cell.bbox and cell.text.upper() in {"C", "D"}
                 and abs(cell.bbox["top"] - line.bbox["top"]) < .006
                 and -.003 <= cell.bbox["left"] - (line.bbox["left"] + line.bbox["width"]) < .025]
        if len(signs) > 1:
            fail("Há mais de um indicador C/D junto ao mesmo valor.")
        indicator = signs[0] if signs else ""
    if indicator in {"C", "D"}:
        if token.startswith(("+", "-")) and (token[0] == "-") != (indicator == "D"):
            fail()
        return abs(value) * (-1 if indicator == "D" else 1)
    if match[2]:
        fail("Um indicador C/D não está legível e precisa dos controles independentes da linha.")
    if require_sign and value != 0 and not match[1].lstrip().startswith(("+", "-")):
        fail("Um valor ou saldo Caixa está sem seu indicador C/D.")
    return value


def parse_period_cash_columns(source):
    text = upper(" ".join(line.text for page in source.pages for line in page.lines))
    nordeste = all(marker in text for marker in ("BANCO DO NORDESTE", "DETALHAMENTO DO EXTRATO", "AGENCIA/CONTA CORRENTE"))
    caixa = all(marker in text for marker in ("GERENCIADOR", "CAIXA", "EXTRATO POR PERIODO", "DATA MOV.", "NR. DOC."))
    if not (nordeste or caixa):
        return None
    labels = {}
    for line in source.pages[0].lines:
        if line.bbox:
            labels.setdefault(upper(line.text), line)
    names = ("HISTORICO", "DOCUMENTO", "VALOR R$", "SALDO R$") if nordeste else ("HISTORICO", "NR. DOC.", "VALOR", "SALDO")
    if not all(key in labels for key in names):
        return None
    history_x, ref_x, value_x, balance_x = [labels[key].bbox["left"] for key in names]
    # Either document/history order is allowed; cash roles remain separate.
    if not max(history_x, ref_x) < value_x < balance_x:
        fail()
    boundary = (value_x + balance_x) / 2
    slope = row_slope([labels[key] for key in names])
    period = re.search(rf"PERIODO:\s*({DATE})\s+ATE\s+({DATE})", text)
    if nordeste:
        start, end = optional_document_period(period)
        seed = re.search(rf"SALDO ANTERIOR:\s*R\$\s*([+-]?\s*{NUMBER})", text)
        if seed is None:
            fail()
        opening = running = money(re.sub(r"\s", "", seed[1]))
    else:
        month = re.search(r"(?:MES:\s*)?(" + "|".join(_MONTHS) + r")/(\d{4})", text)
        bounds = re.search(r"PERIODO:\s*(\d{1,2})\s*-?\s+(\d{1,2})\b", text)
        if month and bounds:
            start, end = [day(f"{int(value):02d}/{_MONTHS.index(month[1])+1:02d}/{month[2]}") for value in bounds.groups()]
        else:
            start, end = optional_document_period(None)
        opening = running = None
    if start is not None and start > end:
        fail()
    rows, checks = [], 0
    for page_index, page in enumerate(source.pages):
        lines = [line for line in page.lines if line.bbox]
        stop = min((line.bbox["top"] for line in lines if upper(line.text) == "DETALHAMENTO DO SALDO"), default=1)
        top = max(labels[key].bbox["top"] for key in names) if page_index == 0 else 0
        cash = [line for line in lines if top < line.bbox["top"] < stop and line.bbox["left"] > value_x - .09
                and _CASH.fullmatch(line.text.strip())]
        used = set()
        anchors = sorted([line for line in lines if line.bbox["left"] < min(history_x, ref_x)
                          and top < line.bbox["top"] < stop and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"])
        seeds = [line for line in lines if upper(line.text) == "SALDO ANTERIOR"] if caixa else []
        row_anchors = [*seeds, *anchors]
        descriptions = [line for line in lines if top < line.bbox["top"] < stop
                        and abs(line.bbox["left"]-history_x) < .07]
        slopes = local_row_slopes(row_anchors, descriptions, fallback=slope) if caixa else None
        assigned = {line.id: nearest_row(line, row_anchors, slope=slope, slopes=slopes) for line in cash}
        if caixa:
            for seed in seeds:
                if opening is not None or rows:
                    fail()
                values = [line for line in cash if assigned[line.id] is seed]
                balances = [line for line in values if line.bbox["left"] > boundary]
                amounts = [line for line in values if line.bbox["left"] <= boundary]
                if len(balances) != 1 or len(amounts) != 1 or _value(amounts[0]) != 0:
                    fail()
                opening = running = _value(balances[0], require_sign=True, cells=lines)
                used.update(line.id for line in values)
        for index, anchor in enumerate(anchors):
            y = anchor.bbox["top"]
            low = (anchors[index - 1].bbox["top"] + y) / 2 if index else max(top, y - .012)
            high = (anchors[index + 1].bbox["top"] + y) / 2 if index + 1 < len(anchors) else min(stop, y + .03)
            cells = [line for line in lines if low <= line.bbox["top"] < high]
            values = [line for line in cash if assigned[line.id] is anchor and line.id not in used]
            cells = list({line.id: line for line in [*cells, *values]}.values())
            amounts = [line for line in values if line.bbox["left"] <= boundary]
            balances = [line for line in values if line.bbox["left"] > boundary]
            if running is None or len(amounts) != 1 or len(balances) != 1:
                fail("Uma linha do extrato está sem valor ou saldo associado.")
            used.update(line.id for line in values)
            date = day(anchor.text)
            if not period_contains(date, start, end) or rows and date < rows[-1].transaction.date:
                fail()
            history_left = anchor.bbox["left"] + anchor.bbox["width"] - .02
            description = " ".join(line.text for line in sorted(cells, key=lambda line: (line.bbox["top"], line.bbox["left"]))
                                   if (history_left <= line.bbox["left"] < ref_x - .025 if nordeste
                                       else abs(line.bbox["left"] - history_x) < .07))
            refs = [line.text for line in cells if abs(line.bbox["left"] - ref_x) < .025 and re.fullmatch(r"\d+", line.text)]
            if not description or len(refs) != 1:
                fail()
            value = _value(amounts[0], require_sign=caixa, cells=cells)
            balance_match = _CASH.fullmatch(balances[0].text.strip())
            recovered_indicator = caixa and balance_match[2] == "€"
            if recovered_indicator:
                magnitude = abs(money(re.sub(r"\s", "", balance_match[1])))
                balance = running + value
                if (abs(balance) != magnitude or balance_match[1].lstrip().startswith(("+", "-"))
                        and money(re.sub(r"\s", "", balance_match[1])) != balance):
                    fail("O indicador Caixa ilegível não confere com os demais valores da linha.")
            else:
                balance = _value(balances[0], require_sign=caixa, cells=cells)
            if caixa and upper(description) == "SALDO DIA":
                if value != 0 or balance != running:
                    fail()
                checks += 1
                continue
            amount = balance - running
            match = _CASH.fullmatch(amounts[0].text.strip())
            explicit = caixa or bool(match[2]) or match[1].lstrip().startswith(("+", "-"))
            if abs(amount) != abs(value) or explicit and amount != value:
                fail("O valor do lançamento não confere com os dois saldos impressos.")
            row = _provenance(_row(page.page_number, anchor.line_index, date, description, amount, balance, refs[0]), page, cells)
            if recovered_indicator:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "balance_indicator_from_independent_controls"])
            if not explicit and amount < 0:
                row = replace(row, warnings=[*row.warnings, "amount_sign_from_printed_balances"])
            if re.match(r"\s*([+-])\s*\1", amounts[0].text):
                row = replace(row, warnings=[*row.warnings, "ocr_duplicate_sign_normalized"])
            rows.append(row)
            running = balance
            checks += 1
        if used != {line.id for line in cash}:
            fail("Há um valor sem lançamento associado neste extrato.")
    if not rows:
        fail()
    layout = "banco_do_nordeste_periodo_cash_ocr_v1" if nordeste else "caixa_periodo_cash_columns_ocr_v1"
    return VerifiedStatement(rows, layout, opening, running, printed_balance_checks=checks, candidate_rows=len(rows))
