"""Cora's older daily groups print signed amounts before the history."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, _row, day, fail, money


def parse_cora_daily_groups(lines):
    summaries = {}
    labels = {"SALDO INICIAL DO PERIODO": "opening", "SALDO FINAL DO PERIODO": "closing",
              "TOTAL DE ENTRADAS": "inflows", "TOTAL DE SAIDAS": "outflows"}
    rows, controls, current = [], {}, None
    groups = []
    for line in lines:
        text = upper(line.text)
        summary = next((key for key in labels if text.startswith(key)), None)
        if summary:
            match = re.fullmatch(re.escape(summary)+rf"\s+[+-]?\s*R\$\s*({NUMBER})", text)
            if match is None or labels[summary] in summaries:
                fail("Um total Cora está ilegível ou duplicado.")
            summaries[labels[summary]] = money(match[1])
            continue
        group = re.fullmatch(rf"({DATE})\s+SALDO DO DIA\s+R\$\s*(-?{NUMBER})", text)
        if group:
            current = day(group[1])
            if current in controls:
                fail("Há controles Cora repetidos sem separação inequívoca.")
            controls[current] = money(group[2])
            groups.append(current)
            continue
        movement = re.fullmatch(rf"([+-])\s*R\$\s*({NUMBER})(?!\d)\s*(.+)", line.text.strip())
        if movement:
            if current is None:
                fail("Um movimento Cora está sem data de grupo.")
            rows.append(_row(line.page_number,line.line_number,current,movement[3].strip(),
                             money(movement[2])*(-1 if movement[1] == "-" else 1)))
        elif re.match(r"^[+-]?\s*R\$", text) or re.match(r"^\d{2}/\d{2}/", text) and "SALDO DO DIA" in text:
            fail("Uma data ou um valor Cora está ilegível.")
    if not rows or set(summaries) != set(labels.values()):
        fail("Faltam os totais ou os movimentos do extrato Cora.")
    if groups != sorted(groups,reverse=True):
        fail("A ordem dos grupos diários Cora não pode ser confirmada.")
    rows.reverse()
    running = summaries["opening"]
    for i,row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if i+1 == len(rows) or rows[i+1].transaction.date != row.transaction.date:
            balance = controls[row.transaction.date]
            if running != balance:
                financial_mismatch()
            rows[i] = replace(row,running_balance=float(balance))
    if (running != summaries["closing"]
            or sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount>0),Decimal(0)) != summaries["inflows"]
            or -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount<0),Decimal(0)) != summaries["outflows"]):
        financial_mismatch()
    return rows
