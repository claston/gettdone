"""PagBank's explicit issuer and three native columns, with daily controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_CASH = re.compile(rf"([+-]?)\s*R\$\s*({NUMBER})")


def parse_pagbank_native_daily(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    periods = set(re.findall(rf"PERIODO:\s*({DATE})\s+A\s+({DATE})", text))
    if len(periods) != 1:
        fail()
    first, last = next(iter(periods))
    start, end = day(first), day(last)
    if start > end:
        fail()
    rows, opening, running, current, closed, total, checks = [], None, None, None, True, Decimal(0), 0
    for page in source.pages:
        columns = _headers(page, ("DATA", "DESCRICAO", "VALOR"))
        if not columns:
            fail("Uma página PagBank está sem suas três colunas.")
        top = next(line.bbox["top"] for line in page.lines if upper(line.text) == "DATA")
        anchors = sorted((line for line in page.lines if line.bbox["top"] > top
            and line.bbox["left"] < columns["DESCRICAO"]-.03 and re.fullmatch(DATE, line.text)), key=lambda cell: cell.bbox["top"])
        cash = [line for line in page.lines if line.bbox["top"] > top and line.bbox["left"] >= columns["VALOR"]-.10
                and "R$" in line.text]
        used = set()
        for index, anchor in enumerate(anchors):
            stop = anchors[index+1].bbox["top"] if index+1 < len(anchors) else 1
            cells = [line for line in page.lines if anchor.bbox["top"]-.006 <= line.bbox["top"] < stop-.006]
            amounts = [line for line in cash if line in cells]
            history = [line for line in cells if columns["DESCRICAO"]-.02 <= line.bbox["left"] < columns["VALOR"]-.10]
            if len(amounts) != 1 or not history or not (match := _CASH.fullmatch(amounts[0].text)):
                fail("Um movimento ou saldo PagBank não tem valor e histórico inequívocos.")
            date = day(anchor.text)
            if not start <= date <= end:
                financial_mismatch()
            if current is None or date != current:
                if not closed or (current is not None and date < current):
                    fail("Um dia PagBank está sem seu fechamento ou fora de ordem.")
                current, closed, total = date, False, Decimal(0)
            description = " ".join(line.text for line in history)
            value = money(match[2]) * (-1 if match[1] == "-" else 1)
            used.add(amounts[0].id)
            if upper(description) == "SALDO DO DIA":
                if closed or not rows or rows[-1].transaction.date != date:
                    fail()
                if opening is None:
                    opening = value-total
                else:
                    checks += 1
                    if running + total != value:
                        financial_mismatch()
                running, closed = value, True
                control = _source(rows[-1], page, cells)
                rows[-1] = replace(rows[-1], running_balance=float(value),
                    source_regions=(*rows[-1].source_regions, *control.source_regions),
                    source_line_refs=(*rows[-1].source_line_refs, *control.source_line_refs))
            else:
                if closed:
                    fail("Há um lançamento PagBank após o saldo do mesmo dia.")
                total += value
                rows.append(_source(_row(page.page_number, anchor.line_index, date, description, value, None), page, cells))
        if used != {line.id for line in cash}:
            fail("Há um valor PagBank sem lançamento ou saldo associado.")
    if not rows or opening is None or not closed or checks < 1:
        fail("O extrato PagBank não tem controles diários suficientes.")
    rows[0] = replace(rows[0], warnings=[*rows[0].warnings, "manual_review_recommended"])
    return VerifiedStatement(rows, "pagbank_native_daily_cash_v1", opening, running, opening_inferred=True,
        printed_balance_checks=checks, candidate_rows=len(rows), balance_checkpoints={})
