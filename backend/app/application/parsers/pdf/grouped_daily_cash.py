"""Grouped daily cash with separate movement/balance columns and printed totals."""
import re
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from app.application.errors import UnsupportedDocumentContentError
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import native_header_columns
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.pdf_document_segmentation import _statement_identity, segment_pdf_pages

_VALUE = re.compile(rf"([+-]?)\s*(?:R\$\s*)?({NUMBER})")


def is_grouped_daily_cash(text):
    text = upper(text)
    return all(label in text for label in ("SALDO FINAL DO PERIODO", "SALDO INICIAL DO PERIODO",
        "TOTAL DE ENTRADAS", "TOTAL DE SAIDAS", "VALORES EM R$", "SALDO EM R$"))


def _value(line):
    match = _VALUE.fullmatch(line.text.strip())
    if not match:
        fail("Uma célula financeira do extrato contém valor ou sinal ilegível.")
    return money(match[2]) * (-1 if match[1] == "-" else 1)


def parse_grouped_daily_cash(source):
    # Native content-stream order may put counterparty details before the
    # owner. Physical header order restores identity without weakening checks.
    pages = ["\n".join(line.text for line in sorted(p.lines, key=lambda line: (line.bbox['top'], line.bbox['left'])))
             for p in source.pages]
    segment_pdf_pages(pages)
    owners = {identity.rsplit("|", 1)[0] for text in pages if (identity := _statement_identity(text))}
    if len(owners) > 1:
        raise UnsupportedDocumentContentError(document_type="multiple_bank_statements",
            message="Este PDF contém contas diferentes. Separe os extratos para preservar a associação dos movimentos.")
    first = source.pages[0]
    columns = native_header_columns(first, ("DATA", "LANCAMENTOS", "VALORES EM R$", "SALDO EM R$"))
    if not columns:
        fail("As colunas de movimentos e saldos não podem ser separadas.")
    labels = ("SALDO FINAL DO PERIODO", "SALDO INICIAL DO PERIODO", "TOTAL DE ENTRADAS", "TOTAL DE SAIDAS")
    headings = [next((line for line in first.lines if upper(line.text) == label), None) for label in labels]
    if any(line is None for line in headings):
        fail()
    summary = []
    for i, label in enumerate(headings):
        right = headings[i+1].bbox['left'] if i+1 < len(headings) else 1
        cells = [line for line in first.lines if label.bbox['top'] < line.bbox['top'] < label.bbox['top']+.04
                 and label.bbox['left']-.01 <= line.bbox['left'] < right and _VALUE.fullmatch(line.text.strip())]
        if len(cells) != 1:
            fail("Um total do extrato está ausente ou ambíguo.")
        summary.append(_value(cells[0]))
    closing, opening, credits, debits = summary
    rows, controls, current = [], {}, None
    date_source = None
    for page in source.pages:
        lines = sorted(page.lines, key=lambda line: (line.bbox['top'], line.bbox['left']))
        header = next((line.bbox['top'] for line in lines if upper(line.text) == "VALORES EM R$"), 0)
        body = [line for line in lines if header+.005 < line.bbox['top'] < .95]
        amounts = [line for line in body if columns['VALORES EM R$']-.02 <= line.bbox['left'] < columns['SALDO EM R$']-.02
                   and re.search(r"\d[,.]\d", line.text)]
        for line in body:
            if re.fullmatch(DATE, line.text) and line.bbox['left'] < columns['LANCAMENTOS']:
                current = day(line.text)
                date_source = page, line
            elif line.bbox['left'] < columns['LANCAMENTOS'] and re.match(r"^\d{1,2}/", line.text):
                fail("Uma data de movimento está ilegível; a data anterior não pode substituí-la.")
            elif upper(line.text) == "SALDO DO DIA":
                cells = [cell for cell in body if cell.bbox['left'] >= columns['SALDO EM R$']-.02
                         and abs(cell.bbox['top']-line.bbox['top']) < .004]
                if current is None or len(cells) != 1 or current in controls:
                    fail("Um saldo diário não pode ser associado à sua data.")
                controls[current] = _value(cells[0])
            elif line in amounts:
                if current is None:
                    fail("Um movimento está sem data explícita.")
                following = min((cell.bbox['top'] for cell in body if cell.bbox['top'] > line.bbox['top']+.005
                    and (cell in amounts or cell.bbox['left'] < columns['LANCAMENTOS'])), default=.95)
                history = [cell for cell in body if columns['LANCAMENTOS']-.01 <= cell.bbox['left'] < columns['VALORES EM R$']-.02
                           and line.bbox['top']-.004 <= cell.bbox['top'] < following-.004]
                if not history:
                    fail("O histórico de um movimento está ausente.")
                row = _source(_row(page.page_number, line.line_index, current, " ".join(cell.text for cell in history),
                                   _value(line), None), page, [*history, line])
                date_evidence = _source(row, date_source[0], [date_source[1]])
                rows.append(replace(row, source_regions=(*row.source_regions, *date_evidence.source_regions),
                                    source_line_refs=(*row.source_line_refs, *date_evidence.source_line_refs)))
    if not rows:
        fail()
    rows.sort(key=lambda row: row.transaction.date)
    daily = defaultdict(lambda: Decimal(0))
    for row in rows:
        daily[row.transaction.date] += Decimal(str(row.transaction.amount))
    if set(daily) != set(controls):
        fail("Há movimentos sem fechamento diário ou páginas ausentes.")
    running = opening
    for date, total in sorted(daily.items()):
        running += total
        if running != controls[date]:
            financial_mismatch()
    actual_credits = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    actual_debits = -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if running != closing or actual_credits != credits or actual_debits != debits:
        financial_mismatch()
    for i, row in enumerate(rows):
        if i+1 == len(rows) or rows[i+1].transaction.date != row.transaction.date:
            rows[i] = replace(row, running_balance=float(controls[row.transaction.date]))
    return VerifiedStatement(rows, "grouped_daily_cash_columns_v1", opening, closing,
                             printed_balance_checks=len(controls)+3)
