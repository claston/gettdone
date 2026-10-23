"""Native portal statements: inherited dates and explicit debit/credit reports."""
import calendar
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _lines, _row, day, fail, money


def parse_followup_native(pages, kind):
    if kind == "banrisul_portal_monthly_v1":
        return _portal(pages, kind)
    return _signed_report(pages, kind)


def _portal(pages, kind):
    if any("BVP-PORTAL BANRISUL" not in normalize_upper_text(page) for page in pages):
        fail("Envie apenas as páginas do mesmo extrato Banrisul, na ordem original.")
    text = normalize_upper_text("\n".join(pages))
    periods = set(re.findall(r"PERIODO:\s*(\d{2})/(\d{4})", text))
    if len(periods) != 1:
        fail()
    month, year = map(int, next(iter(periods)))
    if not 1 <= month <= 12:
        fail()
    end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    start = f"{year:04d}-{month:02d}-01"
    opening = re.findall(rf"SALDO ANT EM\s+({DATE})\s+({NUMBER})(-?)", text)
    if len(opening) != 1 or day(opening[0][0]) >= start:
        fail()
    seed = money(opening[0][1]) * (-1 if opening[0][2] else 1)
    running, current = seed, None
    rows, checked, candidates = [], 0, 0
    for page, line, raw in _lines(pages):
        match = re.fullmatch(rf"(?:({DATE})\s+)?(\d{{4}})\s+(.+?)\s+([A-Z0-9]{{6}})\s+({NUMBER})(-?)\s+([A-Z]{{3}})(?:\s+\d+)?", raw)
        candidate = re.match(rf"^(?:{DATE}\s+)?\d{{4}}\s+\D", raw)
        if candidate:
            candidates += 1
            if not match:
                fail("Não foi possível associar um lançamento Banrisul às colunas de documento e valor.")
        if match:
            if match[1]:
                date = day(match[1])
                if not start <= date <= end or current and date < current:
                    fail()
                current = date
            if current is None:
                fail()
            amount = money(match[5]) * (-1 if match[6] else 1)
            running += amount
            rows.append(_row(page, line, current, match[3], amount, reference=match[4]))
        elif normalize_upper_text(raw).startswith("SALDO NA DATA"):
            control = re.fullmatch(rf"SALDO NA DATA\s+({NUMBER})(-?)", normalize_upper_text(raw))
            if not control or not rows or money(control[1]) * (-1 if control[2] else 1) != running:
                fail("Os lançamentos Banrisul não fecham com o saldo diário impresso.")
            rows[-1] = replace(rows[-1], running_balance=float(running),
                               source_line_refs=(*rows[-1].source_line_refs, (page, line)))
            checked += 1
    if not rows or len(rows) != candidates or not checked or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, kind, seed, running, printed_balance_checks=checked, candidate_rows=candidates)


def _signed_report(pages, kind):
    rows, candidates = [], 0
    currency = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
    for page, line, raw in _lines(pages):
        if not re.match(rf"^{DATE}\b", raw):
            continue
        candidates += 1
        match = re.fullmatch(rf"({DATE})\s+(.+?)\s+({currency})", raw)
        if not match:
            fail("Não foi possível ler um lançamento do relatório de conta corrente.")
        date, description, value = match.groups()
        amount = Decimal(value.replace(",", ""))
        label = normalize_upper_text(description)
        debit = label.startswith(("APLICACAO", "PIX ENVIADO", "PAGAMENTO", "DEBITO", "TARIFA"))
        credit = label.startswith(("PIX RECEBIDO", "RESGATE"))
        if (amount > 0 and debit) or (amount < 0 and credit):
            fail("O sinal do lançamento não corresponde à coluna de débito ou crédito do relatório.")
        rows.append(_row(page, line, day(date), description, amount))
    if not rows or candidates != len(rows):
        fail()
    return VerifiedStatement(rows, kind, None, None, candidate_rows=candidates)
