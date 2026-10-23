"""SISBR descending cash ledger with explicit C/D and daily balance checks."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "sicoob_sisbr_descending_cd_ocr_v1"
_MONEY = re.compile(rf"(?:R\$\s*)?({NUMBER})\s*([CD])")


def parse_sicoob(extraction):
    text = upper("\n".join(line.text for p in extraction.pages for line in p.lines))
    period = re.search(rf"PERIODO\s*:?\s*({DATE})\s+(?:A\s+)?({DATE})", text)
    if not period:
        fail()
    start, end = day(period[1]), day(period[2])
    columns = _headers(extraction.pages[0], ("DATA", "DOCUMENTO", "HISTORICO", "VALOR"))
    if not columns:
        fail()
    rows, controls, current, opening, finished = [], {}, None, None, False
    for page in extraction.pages:
        if finished:
            break
        lines = [item for item in page.lines if item.bbox]
        summary = min((item.bbox["top"] for item in lines if upper(item.text) == "RESUMO"), default=1)
        dates = [item for item in lines if item.bbox["left"] < columns["DOCUMENTO"] - 0.01 and re.fullmatch(r"\d{2}/\d{2}", item.text)]
        labels = [
            item
            for item in lines
            if columns["HISTORICO"] - 0.03 <= item.bbox["left"] < columns["VALOR"] - 0.1
            and upper(item.text) in {"SALDO DO DIA", "SALDO ANTERIOR", "SALDO BLOQUEADO ANTERIOR"}
        ]
        financial = [
            item
            for item in lines
            if item.bbox["left"] >= columns["VALOR"] - 0.1 and item.bbox["top"] < summary and re.search(rf"{NUMBER}", item.text)
        ]
        for value in sorted(financial, key=lambda item: item.bbox["top"]):
            top = value.bbox["top"]
            eligible = [item for item in dates if item.bbox["top"] <= top + 0.006]
            if eligible:
                token = max(eligible, key=lambda item: item.bbox["top"]).text
                year = int(end[:4]) - (int(token[3:5]) > int(end[5:7]))
                current = day(token + f"/{year}")
            near_labels = [item for item in labels if abs(item.bbox["top"] - top) < 0.006]
            if len(near_labels) > 1:
                fail()
            label = upper(near_labels[0].text) if near_labels else ""
            if label == "SALDO BLOQUEADO ANTERIOR":
                if not re.fullmatch(rf"(?:R\$\s*)?{NUMBER}\*", value.text.strip()):
                    fail()
                continue
            signed = _MONEY.fullmatch(value.text.strip())
            if not signed or current is None:
                fail()
            amount = money(signed[1]) * (-1 if signed[2] == "D" else 1)
            if label == "SALDO ANTERIOR":
                if opening is not None or current >= start:
                    fail()
                opening = amount
            elif label == "SALDO DO DIA":
                if current in controls and controls[current] != amount:
                    fail()
                controls[current] = amount
            else:
                if not start <= current <= end:
                    fail()
                boundaries = [item.bbox["top"] for item in [*dates, *labels] if item.bbox["top"] > top + 0.006]
                bottom = min(boundaries, default=summary) - 0.006
                cells = [item for item in lines if top - 0.006 <= item.bbox["top"] < bottom]
                history = [item for item in cells if columns["HISTORICO"] - 0.03 <= item.bbox["left"] < columns["VALOR"] - 0.1]
                docs = [
                    item
                    for item in cells
                    if columns["DOCUMENTO"] - 0.03 <= item.bbox["left"] < columns["HISTORICO"] - 0.03
                    and abs(item.bbox["top"] - top) < 0.006
                ]
                if not history or len(docs) != 1:
                    fail()
                rows.append(
                    _source(
                        _row(
                            page.page_number,
                            value.line_index,
                            current,
                            " ".join(item.text for item in history),
                            amount,
                            reference=docs[0].text,
                        ),
                        page,
                        cells,
                    )
                )
        finished = summary < 1
    if opening is None or not rows or not finished or not controls:
        fail()
    rows.reverse()
    if any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    running = opening
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if index + 1 == len(rows) or rows[index + 1].transaction.date != row.transaction.date:
            if controls.get(row.transaction.date) != running:
                financial_mismatch("Os valores Sicoob não fecham com o saldo diário impresso. Confira o documento original.")
            rows[index] = replace(row, running_balance=float(running))
    closing = re.search(rf"SALDO EM CONTA:\s*({NUMBER})\s*([CD])", text)
    if not closing or money(closing[1]) * (-1 if closing[2] == "D" else 1) != running:
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, running, printed_balance_checks=len(controls) + 1)
