"""Santander's limited app query: only printed Documento cells are references."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_VALUE = re.compile(rf"[+-]?{NUMBER}")


def parse_santander_app_limited(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    period = re.search(rf"PERIODOS:\s*({DATE})\s+A\s*({DATE})", text)
    start, end = optional_document_period(period)
    columns = _headers(source.pages[0], ("DATA", "HISTORICO", "DOCUMENTO", "VALOR", "SALDO"))
    if not columns:
        if any(upper(line.text) == "DATA HISTORICO DOCUMENTO VALOR (R$) SALDO (R$)" for line in source.pages[0].lines):
            return None  # A flowing-text table retains the existing text reader.
        fail()
    if start is not None and start > end:
        fail()
    header = next(line for line in source.pages[0].lines if upper(line.text) == "DATA")
    rows, finished = [], False
    for page in source.pages:
        anchors = sorted((line for line in page.lines if line.bbox["left"] < columns["HISTORICO"]
            and re.match(rf"^{DATE}(?:\s|$)", line.text)), key=lambda line: line.bbox["top"])
        if finished:
            if anchors:
                fail("Há outra tabela após o encerramento da consulta Santander.")
            continue
        footer = [line.bbox["top"] for line in page.lines if upper(line.text) == "SALDO DE CONTAMAX"]
        stop = min(footer, default=1)
        low = header.bbox["top"]+.01 if page.page_number == 1 else 0
        financial = [line for line in page.lines if low < line.bbox["top"] < stop
            and line.bbox["left"] >= columns["VALOR"]-.05 and re.search(r"\d,\d", line.text)]
        used = set()
        for index, anchor in enumerate(anchors):
            if anchor.bbox["top"] >= stop:
                fail()
            high = anchors[index+1].bbox["top"] if index+1 < len(anchors) else stop
            cells = [line for line in page.lines if anchor.bbox["top"]-.006 <= line.bbox["top"] < high-.006]
            values = [line for line in financial if line in cells]
            boundary = (columns["VALOR"] + columns["SALDO"])/2
            amounts = [line for line in values if line.bbox["left"] < boundary]
            balances = [line for line in values if line.bbox["left"] >= boundary]
            documents = [line for line in cells if columns["DOCUMENTO"]-.025 <= line.bbox["left"] < columns["VALOR"]-.06]
            if len(amounts) != 1 or len(balances) != 1 or len(documents) > 1:
                fail("Uma coluna da consulta Santander não pode ser associada ao movimento.")
            if any(not _VALUE.fullmatch(line.text) for line in values):
                fail()
            date = day(anchor.text[:10])
            if not period_contains(date, start, end):
                financial_mismatch()
            if rows and date > rows[-1].transaction.date:
                fail("A ordem decrescente da consulta Santander não está confirmada.")
            history = ([anchor.text[10:].strip()] if anchor.text[10:].strip() else [])
            history += [line.text for line in cells
                        if columns["HISTORICO"]-.025 <= line.bbox["left"] < columns["DOCUMENTO"]-.025]
            if not history:
                fail()
            rows.append(_source(_row(page.page_number, anchor.line_index, date, " ".join(history),
                money(amounts[0].text), money(balances[0].text), documents[0].text if documents else None), page, cells))
            used.update(line.id for line in values)
        if used != {line.id for line in financial}:
            fail("Há um valor Santander sem movimento associado.")
        finished = bool(footer)
    if not rows or not finished:
        fail("A consulta limitada Santander está sem seu encerramento.")
    for newer, older in zip(rows, rows[1:]):
        if Decimal(str(older.running_balance)) + Decimal(str(newer.transaction.amount)) != Decimal(str(newer.running_balance)):
            financial_mismatch()
    return VerifiedStatement(rows, "santander_aplicativo_empresas_conta_corrente_extrato_v1", None, None,
        partial=True, printed_balance_checks=len(rows)-1, candidate_rows=len(rows), balance_checkpoints={})
