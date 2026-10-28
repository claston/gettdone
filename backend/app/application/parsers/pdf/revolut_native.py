"""Revolut BRL: explicit debit/credit columns, row balances and account totals."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.ocr_statement_controls import _provenance
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money

_MONTHS = "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split()
_DATE = re.compile(r"(\d{1,2}) DE ([A-Z]+)\.? DE (\d{4})")
_CASH = re.compile(rf"R\$\s*({NUMBER})")


def _date(text):
    match = _DATE.fullmatch(upper(text))
    if not match:
        fail()
    months = [index for index, month in enumerate(_MONTHS, 1) if month == match[2] or month[:3] == match[2]]
    if len(months) != 1:
        fail()
    return day(f"{int(match[1]):02d}/{months[0]:02d}/{match[3]}")


def parse_revolut_native(source):
    text = upper(" ".join(line.text for page in source.pages for line in page.lines))
    period = re.search(r"TRANSACOES DA CONTA DE (.+?) A (\d{1,2} DE [A-Z]+ DE \d{4})", text)
    start, end = optional_document_period(period, parse_date=_date)
    if start is not None and start > end:
        fail()
    first = source.pages[0]
    labels = {upper(line.text): line for line in first.lines}
    if any(label not in labels for label in ("PRODUTO", "SALDO INICIAL", "SALDO FINAL", "TOTAL")):
        fail("O resumo Revolut está incompleto.")
    total = labels["TOTAL"]
    values = sorted([line for line in first.lines if abs(line.bbox["top"] - total.bbox["top"]) < .008
                     and _CASH.fullmatch(line.text)], key=lambda line: line.bbox["left"])
    if len(values) != 4:
        fail()
    opening, debits, credits, closing = [money(_CASH.fullmatch(line.text)[1]) for line in values]
    if opening + credits - debits != closing:
        fail("O resumo Revolut não fecha com os totais impressos.")
    rows, running = [], opening
    for page in source.pages:
        labels = {upper(line.text): line for line in page.lines}
        if any(key not in labels for key in ("DATA", "DESCRICAO", "SALDO")):
            fail("Uma página Revolut está sem cabeçalho de transações.")
        header = labels["DATA"]
        y = header.bbox["top"]
        words = sorted([word for word in page.words if abs(word.bbox["top"] - y) < .008
                        and upper(word.text) == "VALORES"], key=lambda word: word.bbox["left"])
        if len(words) != 2:
            fail()
        debit_x, credit_x = [word.bbox["left"] for word in words]
        balance_x = labels["SALDO"].bbox["left"]
        boundary = (debit_x + credit_x) / 2
        balance_boundary = (credit_x + balance_x) / 2
        stop = min((line.bbox["top"] for line in page.lines if upper(line.text).startswith("INFORME PERDA OU ROUBO")), default=1)
        dates = [line for line in page.lines if line.bbox["left"] < labels["DESCRICAO"].bbox["left"] - .02
                 and y < line.bbox["top"] < stop and _DATE.fullmatch(upper(line.text))]
        cash = [line for line in page.lines if y < line.bbox["top"] < stop and _CASH.fullmatch(line.text)]
        used = set()
        for index, anchor in enumerate(dates):
            low = anchor.bbox["top"] - .004
            high = dates[index + 1].bbox["top"] - .004 if index + 1 < len(dates) else stop
            cells = [line for line in page.lines if low <= line.bbox["top"] < high]
            values = [line for line in cash if line in cells]
            amounts = [line for line in values if debit_x - .03 < line.bbox["left"] < balance_boundary]
            balances = [line for line in values if line.bbox["left"] >= balance_boundary]
            if len(values) != 2 or len(amounts) != 1 or len(balances) != 1:
                fail("Um movimento Revolut está sem valor ou saldo associado.")
            amount = money(_CASH.fullmatch(amounts[0].text)[1]) * (-1 if amounts[0].bbox["left"] < boundary else 1)
            printed = money(_CASH.fullmatch(balances[0].text)[1])
            date = _date(anchor.text)
            if not period_contains(date, start, end) or rows and date < rows[-1].transaction.date or running + amount != printed:
                fail("Um movimento Revolut não confere com a data ou o saldo impresso.")
            history = [line.text for line in cells if labels["DESCRICAO"].bbox["left"] - .01 <= line.bbox["left"] < debit_x - .03]
            if not history:
                fail()
            rows.append(_provenance(_row(page.page_number, anchor.line_index, date, " ".join(history), amount, printed), page, cells))
            running = printed
            used.update(line.id for line in values)
        if used != {line.id for line in cash}:
            fail("Há um valor Revolut sem transação associada.")
    actual_credit = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    actual_debit = sum((-Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if not rows or running != closing or actual_credit != credits or actual_debit != debits:
        fail("As transações Revolut não conferem com o resumo do extrato.")
    return VerifiedStatement(rows, "revolut_brl_cash_columns_v1", opening, closing, printed_balance_checks=len(rows) + 1,
                             candidate_rows=len(rows))
