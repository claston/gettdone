"""Native InfinitePay grids, including independently printed daily controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.infinitepay_ocr import _DATE, _date, parse_infinitepay
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, fail, money

_SIGNED = re.compile(rf"([+-])\s*({NUMBER})")


def _control(page, anchor):
    cells = [line for line in page.lines if line.bbox["left"] > .8
             and abs(line.bbox["top"] - anchor.bbox["top"]) < .008
             and _SIGNED.fullmatch(line.text.strip())]
    if len(cells) != 1:
        fail("Não foi possível conferir um saldo ou total InfinitePay impresso.")
    match = _SIGNED.fullmatch(cells[0].text.strip())
    return cells[0], money(match[2]) * (-1 if match[1] == "-" else 1)


def parse_infinitepay_native(source):
    summary, controls, pages = {}, {}, []
    labels = {"SALDO INICIAL": "opening", "TOTAL DE ENTRADAS": "credit", "TOTAL DE SAIDAS": "debit",
              "SALDO FINAL DO PERIODO": "closing"}
    for page in source.pages:
        removed = set()
        dates = [line for line in page.lines if _DATE.fullmatch(upper(line.text))]
        for line in page.lines:
            label = upper(line.text)
            if label in labels and .3 < line.bbox["left"] < .8:
                cell, value = _control(page, line)
                key = labels[label]
                if key in summary:
                    fail("O extrato InfinitePay contém mais de um resumo de período.")
                summary[key] = value
                removed.add(cell.id)
            elif label == "SALDO DO DIA":
                preceding = [date for date in dates if date.bbox["top"] < line.bbox["top"]]
                if not preceding:
                    fail()
                date = _date(max(preceding, key=lambda item: item.bbox["top"]).text)
                cell, value = _control(page, line)
                if date in controls and controls[date] != value:
                    fail()
                controls[date] = value
                removed.update((cell.id, line.id))
        pages.append(replace(page, lines=[line for line in page.lines if line.id not in removed]))
    if set(summary) != {"opening", "credit", "debit", "closing"}:
        fail("Falta o resumo financeiro completo do extrato InfinitePay.")
    statement = parse_infinitepay(replace(source, pages=pages))
    rows = statement.rows
    if set(controls) != {row.transaction.date for row in rows}:
        fail("Falta um saldo diário no extrato InfinitePay.")
    incoming = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    outgoing = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if (incoming != summary["credit"] or outgoing != summary["debit"]
            or summary["opening"] + incoming + outgoing != summary["closing"]):
        financial_mismatch()
    running = summary["opening"]
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if index + 1 == len(rows) or rows[index + 1].transaction.date != row.transaction.date:
            printed = controls[row.transaction.date]
            if running != printed:
                financial_mismatch()
            rows[index] = replace(row, running_balance=float(printed))
    return replace(statement, rows=rows, opening=summary["opening"], closing=summary["closing"],
                   printed_balance_checks=len(controls) + 1, candidate_rows=len(rows))
