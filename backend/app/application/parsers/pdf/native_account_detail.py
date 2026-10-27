"""Reverse chronological account details with four summary controls and row balances.

The structural format does not identify an institution: logos may be images,
and a counterparty's bank name must never identify the statement issuer.
"""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "account_detail_descending_cash_v1"
_CASH = re.compile(rf"([+-]?)\s*R\$\s*({NUMBER})")


def _value(line):
    match = _CASH.fullmatch(line.text.strip())
    if match is None:
        fail("Um valor ou saldo do detalhe da conta está ilegível.")
    return money(match[2]) * (-1 if match[1] == "-" else 1)


def _summary(first):
    labels = {"SALDO INICIAL": "opening", "ENTRADAS": "credit", "SAIDAS": "debit", "SALDO FINAL": "closing"}
    summary = {}
    for label, key in labels.items():
        anchors = [line for line in first.lines if upper(line.text) == label]
        if len(anchors) != 1:
            fail("Falta o resumo financeiro completo do detalhe da conta.")
        anchor = anchors[0]
        values = [line for line in first.lines if abs(line.bbox["left"] - anchor.bbox["left"]) < .035
                  and .005 < line.bbox["top"] - anchor.bbox["top"] < .05 and _CASH.fullmatch(line.text.strip())]
        if len(values) != 1:
            fail("Não foi possível associar cada total ao resumo da conta.")
        summary[key] = _value(values[0])
    if summary["credit"] < 0 or summary["debit"] < 0:
        fail()
    return summary


def parse_account_detail(source):
    first = source.pages[0]
    summary = _summary(first)
    text = upper("\n".join(line.text for line in first.lines))
    period = re.search(rf"PERIODO:\s*({DATE})\s+A\s*({DATE})", text)
    if period is None:
        fail("O período do detalhe da conta não está legível.")
    start, end = day(period[1]), day(period[2])
    if start > end:
        fail()
    rows = []
    for page in source.pages:
        labels = {}
        for key in ("DATA", "DESCRICAO", "VALOR", "SALDO"):
            found = [line for line in page.lines if upper(line.text) == key]
            if len(found) != 1:
                fail("Uma página do detalhe da conta está sem cabeçalho de colunas.")
            labels[key] = found[0]
        date_x, history_x, amount_x, balance_x = [labels[key].bbox["left"] for key in ("DATA", "DESCRICAO", "VALOR", "SALDO")]
        if not date_x < history_x < amount_x < balance_x:
            fail()
        top = labels["DATA"].bbox["top"]
        table_bottom = max(line.bbox["top"] + line.bbox["height"] for line in labels.values())
        financial = [line for line in page.lines if line.bbox["left"] > amount_x - .06
                     and line.bbox["top"] > top and "R$" in line.text]
        for line in financial:
            _value(line)
        dates = [line for line in page.lines if abs(line.bbox["left"] - date_x) < .025
                 and line.bbox["top"] > top and re.fullmatch(DATE, line.text.strip())]
        dates.sort(key=lambda line: line.bbox["top"])
        used = set()
        boundary = (amount_x + balance_x) / 2
        for index, anchor in enumerate(dates):
            y = anchor.bbox["top"]
            low = (dates[index - 1].bbox["top"] + y) / 2 if index else table_bottom
            high = (y + dates[index + 1].bbox["top"]) / 2 if index + 1 < len(dates) else 1
            cells = [line for line in page.lines if low <= line.bbox["top"] < high]
            aligned = [line for line in financial if abs(line.bbox["top"] - y) < .008]
            amounts = [line for line in aligned if line.bbox["left"] < boundary]
            balances = [line for line in aligned if line.bbox["left"] >= boundary]
            if len(amounts) != 1 or len(balances) != 1 or any(line.id in used for line in aligned):
                fail("Um movimento está sem valor ou saldo inequívoco no detalhe da conta.")
            date = day(anchor.text.strip())
            if not start <= date <= end:
                financial_mismatch()
            history = [line for line in cells if history_x - .015 <= line.bbox["left"] < amount_x - .06]
            if not history:
                fail("Um movimento está sem descrição no detalhe da conta.")
            witnesses = [anchor, *aligned, *history]
            rows.append(_source(_row(page.page_number, anchor.line_index, date,
                " ".join(line.text for line in history), _value(amounts[0]), _value(balances[0])), page, witnesses))
            used.update(line.id for line in aligned)
        if used != {line.id for line in financial}:
            fail("Há um valor ou saldo sem movimento associado no detalhe da conta.")
    if not rows or any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail("A ordem decrescente dos movimentos da conta não pôde ser confirmada.")
    credit = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    debit = sum((-Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    running = summary["opening"]
    for row in reversed(rows):
        printed = Decimal(str(row.running_balance))
        if running + Decimal(str(row.transaction.amount)) != printed:
            financial_mismatch()
        running = printed
    if (credit != summary["credit"] or debit != summary["debit"] or running != summary["closing"]
            or summary["opening"] + credit - debit != summary["closing"]):
        financial_mismatch()
    return VerifiedStatement(rows, LAYOUT, summary["opening"], summary["closing"],
        printed_balance_checks=len(rows) + 1, candidate_rows=len(rows), balance_checkpoints={},
        partial=validate_statement_completeness(["\n".join(line.text for line in page.lines) for page in source.pages]))
