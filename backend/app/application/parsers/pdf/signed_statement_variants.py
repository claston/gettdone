"""Signed native statement variants with explicit, scope-aware balance controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _lines, _row, day, fail, money

TOTALBANK = "bradesco_totalbank_signed_daily_v1"
BB_COMBINED = "bb_cash_rende_facil_combined_v1"
SPLIT_MONTH = "abbreviated_month_signed_statement_v1"


def identify_variant(text):
    text = upper(text)
    if "PORTAL.TOTALBANK.COM.BR/BRADESCO/VIEWS/EXTRATO.XHTML" in text and "AGENCIA/CONTA" in text:
        return TOTALBANK
    if "CONTA CORRENTE + RENDE FACIL" in text and "BB.COM.BR" in text:
        return BB_COMBINED
    if ("EXTRATO-CNPJ:" in text and "GERADO" in text
            and re.search(r"\b\d{2}\s+-?R\$", text) and re.search(r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/\d{2}\b", text)):
        return SPLIT_MONTH
    return None


def parse_variant(pages, kind):
    return {TOTALBANK: _totalbank, BB_COMBINED: _combined, SPLIT_MONTH: _split_month}[kind](pages, kind)


def _totalbank(pages, kind):
    rows, controls, accounts = [], {}, set()
    opening = None
    finished = False
    pattern = re.compile(rf"^(\d{{3,6}}-[\d-]+)\s+(\d{{2}}/\d{{2}}/\d{{4}})\s+(.+?)\s+R\$\s*([+-]?{NUMBER})$")
    for page, line, raw in _lines(pages):
        if "DEMONSTRATIVO DE INVESTIMENTO" in upper(raw):
            finished = True
        if finished:
            continue
        match = pattern.fullmatch(raw)
        if not match:
            if re.match(r"^\d{3,6}-[\d-]+\s+\d{2}/\d{2}/\d{4}\b", raw):
                fail()
            continue
        account, date, label, value = match.groups()
        accounts.add(account)
        if len(accounts) != 1:
            fail()
        date, amount = day(date), money(value)
        normalized = upper(label)
        if normalized == "SALDO ANTERIOR":
            if opening is not None or rows or controls:
                fail()
            opening = amount
        elif normalized == "SALDO DO DIA":
            controls[date] = amount  # Last control represents the complete day.
        else:
            ref = re.fullmatch(r"(.+?)\s+(\d+)", label)
            if opening is None or ref is None or rows and date < rows[-1].transaction.date:
                fail()
            rows.append(_row(page, line, date, ref[1], amount, reference=ref[2]))
    if opening is None or not rows or not controls or any(row.transaction.date not in controls for row in rows):
        fail()
    running = opening
    for date, control in sorted(controls.items()):
        indices = [i for i, row in enumerate(rows) if row.transaction.date == date]
        running += sum((Decimal(str(rows[i].transaction.amount)) for i in indices), Decimal(0))
        if running != control:
            financial_mismatch()
        if indices:
            rows[indices[-1]] = replace(rows[indices[-1]], running_balance=float(control))
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=len(controls))


def _combined(pages, kind):
    rows = []
    for page, line, raw in _lines(pages):
        if not re.match(r"^\d{2}/\d{2}/\d{4}\b", raw):
            continue
        match = re.fullmatch(rf"(\d{{2}}/\d{{2}}/\d{{4}})\s+\d+-\w\s+(.+?)\s+(\d+)\s+(?:\d{{5}}\s+)?({NUMBER})\s+([CD])", raw)
        if not match:
            fail()
        date, label, reference, value, sign = match.groups()
        if upper(label) == "SALDO DO DIA":
            continue  # Cash + investment is not a cash ledger checkpoint.
        rows.append(_row(page, line, day(date), label, money(value) * (-1 if sign == "D" else 1), reference=reference))
    if not rows:
        fail()
    rows[0] = replace(rows[0], warnings=["combined_cash_investment_balance_not_comparable"])
    return VerifiedStatement(rows, kind, None, None)


def _split_month(pages, kind):
    months = {name: i for i, name in enumerate("JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ".split(), 1)}
    entries = _lines(pages)
    rows, current = [], None
    pattern = re.compile(rf"^(?:(\d{{1,2}})\s+)?(-?)R\$\s*({NUMBER})$")
    for index, (page, line, raw) in enumerate(entries):
        match = pattern.fullmatch(raw)
        if not match:
            if "R$" in raw:
                fail()
            continue
        if index + 1 >= len(entries):
            fail()
        description = entries[index+1][2]
        month = re.match(r"^(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/(\d{2})\s+(.+)$", upper(description))
        if match[1]:
            if month is None:
                fail()
            current = day(f"{int(match[1]):02d}/{months[month[1]]:02d}/20{month[2]}")
            description = month[3]
        elif current is None or month:
            fail()
        if not re.search(r"[A-Za-zÀ-ÿ]", description) or "R$" in description:
            fail()
        rows.append(_row(page, line, current, description, money(match[3]) * (-1 if match[2] else 1)))
    if not rows:
        fail()
    return VerifiedStatement(rows, kind, None, None)


def parse_bb_split(pages, kind):
    """BB's signed ledger without PERIODO; zero-date controls inherit the movement day."""
    rows, opening, running, closing, current = [], None, None, None, None
    pending = None
    checks = 0
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        if normalized.startswith(("EXTRATO", "CLIENTE", "AGENCIA", "LANCAMENTOS", "DIA ", "TOTAL APLICACOES", "* SALDOS", "SUJEITOS")):
            continue
        dated = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$", raw)
        if dated:
            if pending:
                fail()
            date, content = dated.groups()
            if date != "00/00/0000":
                current = day(date)
            elif "SALDO DO DIA" not in normalized:
                fail()
        else:
            date, content = None, raw
        value = re.fullmatch(rf"(.+?)\s+({NUMBER})\s*\(([+-])\)", content)
        if not value:
            if dated:
                pending = (page, line, current, content)
            elif pending is None and rows and closing is None:
                previous = rows[-1]
                rows[-1] = replace(previous,
                                   transaction=replace(previous.transaction, description=previous.transaction.description+" "+raw),
                                   source_line_refs=(*previous.source_line_refs, (page, line)))
            continue
        label, amount = value[1], money(value[2]) * (-1 if value[3] == "-" else 1)
        compact = re.sub(r"\s", "", upper(label))
        if "SALDOANTERIOR" in compact:
            if opening is not None or rows or pending:
                fail()
            opening = running = amount
        elif "SALDODODIA" in compact or compact == "SALDO":
            if pending or running is None or running != amount or not rows:
                fail()
            rows[-1] = replace(rows[-1], running_balance=float(amount), source_line_refs=(*rows[-1].source_line_refs, (page, line)))
            checks += 1
            if compact == "SALDO":
                closing = amount
        else:
            source_page, source_line, transaction_date = page, line, current
            if pending:
                source_page, source_line, transaction_date, title = pending
                label = title + " " + label
                pending = None
            if running is None or closing is not None or transaction_date is None or rows and transaction_date < rows[-1].transaction.date:
                fail()
            label = re.sub(r"^\d+\s+(?:\d+\s+)?", "", label)
            running += amount
            row = _row(source_page, source_line, transaction_date, label, amount)
            if (source_page, source_line) != (page, line):
                row = replace(row, source_line_refs=(*row.source_line_refs, (page, line)))
            rows.append(row)
    if pending or not rows or closing is None:
        fail()
    return VerifiedStatement(rows, kind, opening, closing, printed_balance_checks=checks)
