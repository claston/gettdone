"""NDD's ID/date/type table, checked against count, totals and daily balances."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "ndd_conta_digital_operacoes_v1"


def compact(text):
    return re.sub(r"\s+", "", upper(text))


def _right_value(page, anchor, *, integer=False, dash_zero=False):
    candidates = [line for line in page.lines if line.bbox["left"] > anchor.bbox["left"] + .1
                  and abs(line.bbox["top"] - anchor.bbox["top"]) < .008]
    pattern = r"\d+" if integer else rf"(?:R\$\s*)?[+-]?{NUMBER}"
    values = [line for line in candidates if re.fullmatch(pattern, line.text.strip())
              or dash_zero and compact(line.text) == "R$-"]
    if len(values) != 1:
        fail("Não foi possível conferir um total ou saldo NDD impresso.")
    value = values[0].text.strip()
    return Decimal(0) if compact(value) == "R$-" else Decimal(value) if integer else money(value.removeprefix("R$").strip())


def parse_ndd_native(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    bounds = re.search(rf"\bDE:\s*({DATE}).*?ATE:\s*({DATE})", text, re.S)
    start, end = optional_document_period(bounds)
    if start is not None and start > end:
        fail()
    totals = {key: set() for key in ("opening", "closing", "credit", "debit", "count")}
    labels = {"TOTALGERAL:": "closing", "TOTALCREDITO:": "credit", "TOTALDEBITO:": "debit",
              "QUANTIDADEDEOPERACOES:": "count"}
    controls, rows, stamps = {}, [], []
    for page in source.pages:
        for line in page.lines:
            label = compact(line.text)
            if label.startswith("SALDOINICIAL"):
                totals["opening"].add(_right_value(page, line, dash_zero=True))
            elif label in labels:
                key = labels[label]
                totals[key].add(_right_value(page, line, integer=key == "count"))
            elif match := re.fullmatch(rf"SALDOFINALDODIA({DATE}):", label):
                date = day(match[1])
                value = _right_value(page, line)
                if date in controls and controls[date] != value:
                    fail()
                controls[date] = value
        headers = {compact(line.text): line for line in page.lines}
        required = ("DATA/HORA", "LANCAMENTO", "OPERACAO", "DESCRICAODAMOVIMENTACAO", "VALOR")
        if any(key not in headers for key in required):
            fail("O cabeçalho das colunas NDD está incompleto.")
        date_x, type_x, operation_x, history_x, value_x = [headers[key].bbox["left"] for key in required]
        if not date_x < type_x < operation_x < history_x < value_x:
            fail()
        top = headers["VALOR"].bbox["top"]
        stop = min((line.bbox["top"] for line in page.lines if compact(line.text) == "TOTALGERAL:"), default=1)
        dates = [line for line in page.lines if abs(line.bbox["left"] - date_x) < .03
                 and re.fullmatch(rf"{DATE}\s+(?:[01]\d|2[0-3]):[0-5]\d", line.text)]
        cash = [line for line in page.lines if line.bbox["left"] > value_x - .04 and top < line.bbox["top"] < stop
                and re.fullmatch(NUMBER, line.text)]
        used = set()
        for index, anchor in enumerate(dates):
            y = anchor.bbox["top"]
            low = (dates[index - 1].bbox["top"] + y) / 2 if index else top
            high = (y + dates[index + 1].bbox["top"]) / 2 if index + 1 < len(dates) else stop
            cells = [line for line in page.lines if low <= line.bbox["top"] < high]
            baseline = [line for line in cells if abs(line.bbox["top"] - y) < .008]
            values = [line for line in cash if line in baseline]
            types = [line for line in baseline if type_x - .01 <= line.bbox["left"] < operation_x - .01
                     and upper(line.text) in {"CREDITO", "DEBITO"}]
            if len(values) != 1 or len(types) != 1 or values[0].id in used:
                fail("Um movimento NDD está sem valor, tipo ou data legível.")
            date, time = anchor.text.split()
            date = day(date)
            if not period_contains(date, start, end):
                fail()
            history = " ".join(line.text for line in cells if history_x - .01 <= line.bbox["left"] < value_x - .05)
            if not history:
                fail()
            references = [line.text for line in baseline if line.bbox["left"] < date_x - .02
                          and re.fullmatch(r"-?\d+", line.text)]
            amount = money(values[0].text) * (-1 if upper(types[0].text) == "DEBITO" else 1)
            rows.append(_source(_row(page.page_number, anchor.line_index, date, history, amount,
                                     reference=references[0] if len(references) == 1 else None), page, cells))
            stamps.append((date, time))
            used.add(values[0].id)
        if used != {line.id for line in cash}:
            fail("Há um valor NDD sem operação associada.")
    if not rows or any(len(values) != 1 for values in totals.values()):
        fail()
    totals = {key: next(iter(values)) for key, values in totals.items()}
    if totals["count"] != len(rows) or stamps not in (sorted(stamps), sorted(stamps, reverse=True)):
        fail("A quantidade ou a ordem das operações NDD não confere.")
    rows = [row for _, row in sorted(zip(stamps, rows, strict=True), key=lambda pair: pair[0])]
    credit = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    debit = sum((-Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if credit != totals["credit"] or debit != totals["debit"]:
        financial_mismatch()
    running = totals["opening"]
    if set(controls) != {row.transaction.date for row in rows}:
        fail("Falta um saldo diário do extrato NDD.")
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if index + 1 == len(rows) or rows[index + 1].transaction.date != row.transaction.date:
            printed = controls[row.transaction.date]
            if running != printed:
                financial_mismatch()
            rows[index] = replace(row, running_balance=float(printed))
    if running != totals["closing"]:
        financial_mismatch()
    return VerifiedStatement(rows, LAYOUT, totals["opening"], totals["closing"],
                             printed_balance_checks=len(controls) + 1, candidate_rows=len(rows))
