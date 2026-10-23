"""Strict readers for native date/time, grouped-day, inactive and ERP exports."""

import re
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _lines, _row, day, fail, money
from app.application.structured_statement_detection import CONTA_AZUL, CRESOL_INACTIVE, SANTANDER_GROUPED, SUMUP_PERIOD

MONTHS = dict(zip(("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
                   "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"), range(1, 13)))
DATE = r"\d{2}/\d{2}/\d{4}"
SIGNED = rf"[+-]?\s*{NUMBER}"


def parse_structured_statement(pages, kind):
    return {SUMUP_PERIOD: _sumup, SANTANDER_GROUPED: _santander,
            CRESOL_INACTIVE: _cresol_inactive, CONTA_AZUL: _conta_azul}[kind](pages, kind)


def _signed(value):
    return money(re.sub(r"\s+", "", value))


def _append(row, text, page, line):
    return replace(row, transaction=replace(row.transaction, description=row.transaction.description + " " + text),
                   source_line_refs=(*row.source_line_refs, (page, line)))


def _sumup(pages, kind):
    text = "\n".join(pages)
    period = re.search(rf"EXTRATO BANCARIO DE ({DATE}) A ({DATE})", upper(text))
    closing = re.findall(rf"SALDO EM ({DATE})\s+R\$\s*({SIGNED})", upper(text))
    if period is None or len(closing) != 1 or closing[0][0] != period[2]:
        fail()
    start, end = day(period[1]), day(period[2])
    rows, timestamps = [], []
    footer = False
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        if normalized.startswith("AINDA COM DUVIDAS"):
            footer = True
        if footer:
            continue
        if re.match(rf"^{DATE}\b", raw):
            match = re.fullmatch(rf"({DATE})\s+(\d{{2}}:\d{{2}})\s+({SIGNED})\s+(.+)", raw)
            if match is None:
                fail()
            date, hour, amount_text, description = match.groups()
            try:
                timestamp = datetime.strptime(date + " " + hour, "%d/%m/%Y %H:%M")
            except ValueError:
                fail()
            amount = _signed(amount_text)
            if not start <= timestamp.date().isoformat() <= end or timestamps and timestamp > timestamps[-1]:
                fail()
            if ((upper(description).startswith("PIX ENVIADO") and amount >= 0)
                    or (upper(description).startswith(("PIX RECEBIDO", "VENDAS SUMUP")) and amount < 0)):
                fail()
            rows.append(_row(page, line, timestamp.date().isoformat(), description, amount))
            timestamps.append(timestamp)
        elif rows and not normalized.startswith((
            "EXTRATO BANCARIO", "DOCUMENTO GERADO", "AGENCIA:", "DATA E HORA", "SALDO EM", "DADOS DA CONTA",
        )):
            if "R$" in raw:
                fail()
            rows[-1] = _append(rows[-1], raw, page, line)
    if not rows:
        fail()
    # The header prints only a closing balance. The opening is derived, never
    # advertised as an independent printed financial validation.
    closing = _signed(closing[0][1])
    rows.reverse()
    opening = running = closing - sum((Decimal(str(row.transaction.amount)) for row in rows), Decimal(0))
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        rows[index] = replace(row, running_balance=float(running))
    rows[0] = replace(rows[0], warnings=["manual_review_recommended"])
    return VerifiedStatement(rows, kind, opening, closing, candidate_rows=len(rows), opening_inferred=True)


def _santander(pages, kind):
    current = None
    rows = []
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        date = re.search(r"\b(\d{1,2}) DE ([A-Z]+) DE (20\d{2})\b", normalized)
        if date:
            month = MONTHS.get(date[2])
            if month is None:
                fail()
            current = day(f"{int(date[1]):02d}/{month:02d}/{date[3]}")
            continue
        # Contact text can precede real movements in PDF extraction order.
        # Match the movement suffix and resume at any later date group.
        if "R$" not in raw and not re.search(r"\b(?:CREDITO|DEBITO)\b", normalized):
            continue
        match = re.fullmatch(rf"(.+?)\s+(CREDITO|DEBITO)\s+R\$\s*({NUMBER})", normalized)
        if match is None or current is None:
            fail()
        amount = money(match[3]) * (-1 if match[2] == "DEBITO" else 1)
        rows.append(_row(page, line, current, match[1], amount))
    if not rows:
        fail()
    return VerifiedStatement(rows, kind, None, None, candidate_rows=len(rows))


def _cresol_inactive(pages, kind):
    if len(pages) != 1:
        fail()
    text = upper(pages[0])
    period = re.search(rf"PERIODO DE ({DATE}) A ({DATE})", text)
    opening = re.search(rf"([+-])\s*R\$\s*({NUMBER})\s*SALDO ANTERIOR:", text)
    if opening is None:
        opening = re.search(rf"SALDO ANTERIOR:\s*([+-])\s*R\$\s*({NUMBER})", text)
    summary = re.search(rf"SALDO EM CONTA LIMITE DE CREDITO SALDO DISPONIVEL\s+R\$\s*({SIGNED})\s+R\$", text)
    if period is None or day(period[1]) > day(period[2]) or opening is None or summary is None:
        fail()
    balance = money(opening[2]) * (-1 if opening[1] == "-" else 1)
    if balance != _signed(summary[1]) or re.search(rf"(?:^|\n)\s*{DATE}\b", pages[0]):
        fail()
    for raw in pages[0].splitlines():
        normalized = upper(raw)
        if not re.search(NUMBER, normalized):
            continue
        summary_line = re.fullmatch(rf"R\$\s*{SIGNED}\s+R\$\s*{SIGNED}\s+R\$\s*{SIGNED}", normalized)
        opening_line = (re.fullmatch(rf"[+-]\s*R\$\s*{NUMBER}\s*SALDO ANTERIOR:", normalized)
                        or re.fullmatch(rf"SALDO ANTERIOR:\s*[+-]\s*R\$\s*{NUMBER}", normalized))
        if not (summary_line or opening_line):
            fail()
    return VerifiedStatement([], kind, balance, balance, printed_balance_checks=1)


def _conta_azul(pages, kind):
    text = upper("\n".join(pages))
    period = re.search(rf"RELATORIO DE EXTRATO\s+({DATE}) A ({DATE})", text)
    if period is None:
        fail()
    start, end = day(period[1]), day(period[2])
    totals = {}
    for label in ("RECEITAS REALIZADAS", "DESPESAS REALIZADAS", "TOTAIS DO PERIODO"):
        match = re.search(rf"{label}\s*\(R\$\)\s*({SIGNED})", text)
        if match is None:
            fail()
        totals[label] = _signed(match[1])
    rows, opening, running, columns = [], None, None, None
    footer_closing = None
    in_footer = False
    in_table = False
    for page, content in enumerate(pages, 1):
        for line, raw in enumerate(content.splitlines(), 1):
            normalized = upper(raw)
            if all(label in normalized for label in ("DATA", "DESCRICAO", "CLIENTE/FORNECEDOR", "SITUACAO", "VALOR", "SALDO")):
                header_cells = list(re.finditer(
                    r"Data|Descri[cç][aã]o|Cliente/Fornecedor|Categoria|Situa[cç][aã]o|Valor|Saldo", raw, re.I,
                ))
                if len(header_cells) != 7:
                    fail()
                columns = [cell.start() for cell in header_cells]
                in_table = True
                in_footer = False
                continue
            if normalized.strip().startswith(("TOTAIS DO PERIODO", "USUARIO:")):
                in_table = False
                in_footer = bool(rows) and normalized.startswith("TOTAIS DO PERIODO")
                continue
            if in_footer:
                values = re.findall(rf"R\$\s*({SIGNED})", normalized)
                if values:
                    if len(values) != 3 or footer_closing is not None:
                        fail()
                    footer_closing = _signed(values[-1])
                continue
            if re.match(rf"^\s*{DATE}\b", raw):
                if not in_table or columns is None:
                    fail()
                fields = [raw[left:right].strip() for left, right in zip(columns, [*columns[1:], len(raw)], strict=True)]
                date, description, counterparty, _category, status, amount, balance = fields
                if (not re.fullmatch(SIGNED, amount) or not re.fullmatch(SIGNED, balance)
                        or upper(status) not in {"RECEBIDO", "PAGO", "TRANSFERIDO"} or not description):
                    fail()
                date, amount, balance = day(date), _signed(amount), _signed(balance)
                if not start <= date <= end or rows and date < rows[-1].transaction.date:
                    fail()
                if opening is None:
                    opening = running = balance - amount
                running += amount
                if running != balance:
                    financial_mismatch()
                description = " ".join(part for part in (description, counterparty) if part)
                rows.append(_row(page, line, date, description, amount, balance))
            elif in_table and raw.strip() and columns is not None:
                if not rows:
                    fail()
                if re.search(NUMBER, raw[columns[5]:]):
                    fail()
                description = raw[columns[1]:columns[2]].strip()
                counterparty = raw[columns[2]:columns[3]].strip()
                continuation = " ".join(part for part in (description, counterparty) if part)
                if continuation:
                    rows[-1] = _append(rows[-1], continuation, page, line)
    credits = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    debits = -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if (not rows or credits != totals["RECEITAS REALIZADAS"] or debits != totals["DESPESAS REALIZADAS"]
            or credits - debits != totals["TOTAIS DO PERIODO"] or footer_closing != running):
        fail()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=len(rows) + 3, candidate_rows=len(rows))
