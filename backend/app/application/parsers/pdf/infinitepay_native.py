"""Native InfinitePay grids, including independently printed daily controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.infinitepay_ocr import _DATE, _date, parse_infinitepay
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, fail, money
from app.application.source_regions import evidence_regions

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
    balance_evidence = {}
    current_date = None
    labels = {"SALDO INICIAL": "opening", "TOTAL DE ENTRADAS": "credit", "TOTAL DE SAIDAS": "debit",
              "SALDO FINAL DO PERIODO": "closing"}
    for page in source.pages:
        removed = set()
        for line in page.lines:
            label = upper(line.text)
            if _DATE.fullmatch(label) and line.bbox["left"] < .12:
                current_date = _date(line.text)
            if label in labels and .3 < line.bbox["left"] < .8:
                cell, value = _control(page, line)
                key = labels[label]
                if key in summary:
                    fail("O extrato InfinitePay contém mais de um resumo de período.")
                summary[key] = value
                removed.add(cell.id)
            elif label == "SALDO DO DIA":
                if current_date is None:
                    fail()
                cell, value = _control(page, line)
                if current_date in controls:
                    fail()
                controls[current_date] = value
                balance_evidence[current_date] = (page, (line, cell))
                removed.update((cell.id, line.id))
        pages.append(replace(page, lines=[line for line in page.lines if line.id not in removed]))
    partial = validate_statement_completeness(["\n".join(line.text for line in page.lines) for page in source.pages])
    if not summary and partial:
        return _partial_statement(source, pages, controls, balance_evidence)
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
            rows[index] = _with_balance(row, printed, balance_evidence[row.transaction.date])
    return replace(statement, rows=rows, opening=summary["opening"], closing=summary["closing"],
                   printed_balance_checks=len(controls) + 1, candidate_rows=len(rows))


def _with_balance(row, printed, evidence):
    page, cells = evidence
    return replace(row, running_balance=float(printed),
        source_regions=[*row.source_regions, *evidence_regions(page.page_number, cells, words=page.words)],
        source_line_refs=tuple(dict.fromkeys([*row.source_line_refs, *((page.page_number, cell.line_index) for cell in cells)])))


def _partial_statement(source, pages, controls, balance_evidence):
    """Use printed checkpoints without inventing the fragment's opening/closing."""
    statement = parse_infinitepay(replace(source, pages=pages))
    rows = statement.rows
    dates = {row.transaction.date for row in rows}
    if not set(controls) <= dates:
        fail("Há um saldo InfinitePay sem movimentos associados.")
    running = None
    checks = 0
    for index, row in enumerate(rows):
        if running is not None:
            running += Decimal(str(row.transaction.amount))
        if index + 1 == len(rows) or rows[index + 1].transaction.date != row.transaction.date:
            printed = controls.get(row.transaction.date)
            if printed is None:
                # Only the last, truncated day may lack its closing control.
                if index + 1 != len(rows):
                    fail("Falta um saldo diário intermediário no trecho InfinitePay.")
                continue
            if running is not None:
                checks += 1
                if running != printed:
                    financial_mismatch()
            running = printed
            rows[index] = _with_balance(row, printed, balance_evidence[row.transaction.date])
    return replace(statement, rows=rows, partial=True, candidate_rows=len(rows), printed_balance_checks=checks,
                   balance_bounds_unavailable=True)
