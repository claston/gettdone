"""Explicit native export variants with complete row and financial control checks."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import (
    DATE,
    NUMBER,
    VerifiedStatement,
    _lines,
    _row,
    day,
    fail,
    money,
)

INTER = "recovery_inter_printed_values_v1"
CRESOL_DESCENDING = "recovery_cresol_descending_daily_v1"
COLMEIA = "recovery_cresol_colmeia_daily_v1"
BTG_INVESTMENT = "recovery_btg_investment_cash_v1"
BTG_INVOICE = "recovery_btg_card_invoice_v1"
ALELO = "recovery_alelo_prepaid_statement_v1"
_CURRENCY = re.compile(rf"([+-]?)\s*R\$\s*({NUMBER})(?![\d.,])")
_CD = re.compile(rf"({NUMBER})\s*([CD])$")
_MONTHS = dict(zip(("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"), range(1, 13)))


def upper(text):
    # Some Windows print drivers put UTF-8 bytes into a Latin-1 text map.
    # Repair only valid two-byte sequences before matching layout labels.
    repaired = re.sub(r"[\u00c2\u00c3][\u0080-\u00bf]", lambda m: m[0].encode("latin-1").decode("utf-8"), text)
    return normalize_upper_text(repaired)


def identify_afternoon_statement(text):
    normalized = upper(text)
    compact = normalized.replace(" ", "")
    if ("EXTRATOCONSOLIDADODECONTACORRENTE" in compact
            and "INSTITUICAOFINANCEIRA:133" in compact and "CRESOL" in compact):
        return COLMEIA
    if "ALELO - PORTAL PREPAX" in normalized and "EXTRATO CARTAO - DESPESAS CORPORATIVAS" in normalized:
        return ALELO
    if ("BTG PACTUAL" in normalized and "RESUMO DA FATURA ATUAL" in normalized
            and "TOTAL DE COMPRAS E DESPESAS DESSA FATURA" in normalized):
        return BTG_INVOICE
    if ("CONTA INVESTIMENTO" in normalized and re.search(r"BANCO:\s*208\s*BTG", normalized)
            and "DEBITOCREDITOSALDO" in compact):
        return BTG_INVESTMENT
    if ("EXTRATO CONTA CORRENTE" in normalized and "DATA LANCAMENTO DESCRICAO VALOR SALDO" in normalized
            and re.search(r"\bINTER\b", normalized)):
        return INTER
    if all(marker in normalized for marker in (
        "SALDO EM CONTA LIMITE DE CREDITO SALDO DISPONIVEL", "SALDO DO DIA:",
        "CONSULTA POSICAO CONSOLIDADA EM", "SALDO ANTERIOR:",
    )):
        return CRESOL_DESCENDING
    return None


def parse_afternoon_statement(pages, kind):
    return {INTER: _inter, CRESOL_DESCENDING: _descending, COLMEIA: _colmeia,
            BTG_INVESTMENT: _btg_investment, ALELO: _alelo, BTG_INVOICE: _btg_invoice}[kind](pages, kind)


def _period(text):
    match = re.search(rf"PERIODO(?: DE)?\s*:?\s*({DATE})\s*A(?:TE)?\s*({DATE})", upper(text))
    if match is None:
        fail()
    start, end = day(match[1]), day(match[2])
    if start > end:
        fail()
    return start, end


def _one(values):
    if not values or len(set(values)) != 1:
        fail()
    return values[0]


def _within(date, bounds):
    if not bounds[0] <= date <= bounds[1]:
        fail()
    return date


def _inter(pages, kind):
    bounds = _period("\n".join(pages))
    rows, opening, running = [], None, None
    value = r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?"
    pattern = re.compile(rf"^({DATE})\s+(.+?)\s+({value})\s+([+-]?{NUMBER})(?:\s+[^\d].*)?$")
    for page, line, raw in _lines(pages):
        if not re.match(rf"^{DATE}\b", raw):
            continue
        match = pattern.fullmatch(raw)
        if match is None:
            fail()
        date = _within(day(match[1]), bounds)
        amount, balance = money(match[3]), money(match[4])
        if opening is None:
            opening = running = balance - amount
        if rows and date < rows[-1].transaction.date:
            fail()
        if running + amount != balance:
            financial_mismatch()
        rows.append(_row(page, line, date, match[2], amount, balance))
        running = balance
    if not rows:
        fail()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=len(rows))


def _descending(pages, kind):
    from app.application.pdf_document_segmentation import _statement_identity

    groups = []
    identities = set()
    for index, page in enumerate(pages):
        bounds = _period(page)
        identity = _statement_identity(page)
        if identity:
            identities.add(tuple(identity.split("|")[:2]))
        if groups and groups[-1][0] == bounds:
            groups[-1][2].append(page)
        else:
            groups.append((bounds, index, [page]))
    if len(identities) > 1:
        fail("Este arquivo contém contas Cresol diferentes. Envie cada conta separadamente.")
    if len(groups) == 1:
        return _descending_period(pages, kind)
    statements = []
    for bounds, offset, block in sorted(groups, key=lambda group: group[0]):
        statement = _descending_period(block, kind)
        if statements and statements[-1].closing != statement.opening:
            financial_mismatch()
        statement = replace(statement, rows=[replace(row, source_page=row.source_page + offset,
            source_line_refs=tuple((page + offset, line) for page, line in row.source_line_refs)) for row in statement.rows])
        statements.append(statement)
    rows = sorted([row for statement in statements for row in statement.rows], key=lambda row: row.transaction.date)
    return VerifiedStatement(rows, kind, statements[0].opening, statements[-1].closing,
        printed_balance_checks=sum(statement.printed_balance_checks for statement in statements),
        non_cash_entries=sum(statement.non_cash_entries for statement in statements))


def _descending_period(pages, kind):
    text = "\n".join(pages)
    bounds = _period(text)
    rows, controls, openings = [], {}, []
    blocked = 0
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        if normalized.startswith("SALDO ANTERIOR:"):
            values = list(_CURRENCY.finditer(raw))
            if len(values) != 1:
                fail()
            openings.append(_signed_currency(values[0]))
            continue
        dated = re.match(rf"^({DATE})\s+(.+)$", raw)
        if not dated:
            continue
        date = _within(day(dated[1]), bounds)
        values = list(_CURRENCY.finditer(dated[2]))
        if len(values) != 1 or dated[2][values[0].end():].strip():
            fail()
        value = _signed_currency(values[0])
        if upper(dated[2]).startswith("SALDO DO DIA:"):
            if date in controls and controls[date] != value:
                fail()
            controls[date] = value
        else:
            if values[0][1] not in {"+", "-"}:
                fail()
            description = dated[2][:values[0].start()].strip()
            if upper(description).startswith("DEPOSITOS EM CHEQUES BLOQUEADOS -"):
                if value <= 0:
                    fail()
                blocked += 1
                continue
            rows.append(_row(page, line, date, description, value))
    opening = _one(openings)
    if not rows or any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    # The export reverses the complete physical ledger, including same-day rows
    # spanning pages. Repeated daily controls are continuations, not events.
    rows.reverse()
    running = opening
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        date = row.transaction.date
        last_in_day = index + 1 == len(rows) or rows[index + 1].transaction.date != date
        if last_in_day:
            if controls.get(date) != running:
                financial_mismatch()
            rows[index] = replace(row, running_balance=float(controls[date]) if date in controls else None)
    if set(controls) != {r.transaction.date for r in rows}:
        financial_mismatch()
    # The first monetary cell is the printed cash closing, not the credit limit.
    header = re.search(r"SALDO EM CONTA\s+LIMITE DE CREDITO\s+SALDO DISPONIVEL\s+", upper(text))
    if header:
        summary = _CURRENCY.search(upper(text), header.end())
        if summary is None:
            fail()
        if _signed_currency(summary) != running:
            financial_mismatch()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=len(controls) + 1, non_cash_entries=blocked)


def _signed_currency(match):
    return money(match[2]) * (-1 if match[1] == "-" else 1)


def _colmeia(pages, kind):
    bounds = _period("\n".join(pages))
    rows, opening, running, current, closing = [], None, None, None, None
    pending = []
    checks = 0
    for page, line, raw in _lines(pages):
        compact = upper(raw).replace(" ", "")
        control = re.fullmatch(rf"({DATE})SALDOANT\.:({NUMBER})([CD])", compact)
        if control:
            if pending:
                fail()
            current = _within(day(control[1]), bounds)
            value = money(control[2]) * (-1 if control[3] == "D" else 1)
            if opening is None:
                opening = running = value
            elif value != running or rows and current < rows[-1].transaction.date:
                fail()
            checks += 1
            continue
        if compact.startswith("(=)SALDO."):
            value = _CD.search(compact)
            if value is None or pending:
                fail()
            closing = money(value[1]) * (-1 if value[2] == "D" else 1)
            if closing != running:
                fail()
            checks += 1
            break
        if current is None:
            continue
        if (not compact or set(compact) <= {"=", "_"} or compact.startswith(("HTTP", "CRESOL", "EXTRATO"))
                or "COLMEIA" in compact and "SISTEMA" in compact):
            continue
        if re.match(rf"^{DATE}", compact):
            fail()
        amount = _CD.search(compact)
        if amount:
            # Keep physical description/amount fragments together across pages.
            description = " ".join([*pending, re.sub(rf"{NUMBER}\s*[CD]$", "", raw).strip()]).strip()
            if not description:
                fail()
            value = money(amount[1]) * (-1 if amount[2] == "D" else 1)
            running += value
            rows.append(_row(page, line, current, description, value))
            pending = []
        else:
            pending.append(raw)
    if not rows or closing is None or pending:
        fail()
    rows[-1] = replace(rows[-1], running_balance=float(closing))
    return VerifiedStatement(rows, kind, opening, closing, printed_balance_checks=checks)


def _btg_investment(pages, kind):
    bounds = _period("\n".join(pages))
    rows, opening, running, closing = [], None, None, None
    totals = {}
    checks = 0
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        total = re.fullmatch(rf"TOTAL DE (CREDITOS|DEBITOS)\s+({NUMBER})", normalized)
        if total:
            totals[total[1]] = money(total[2])
        match = re.fullmatch(rf"({DATE})\s+(.+?)\s+([+-]?{NUMBER})(?:\s+([+-]?{NUMBER}))?", raw)
        if match is None:
            if re.match(rf"^{DATE}", raw):
                fail()
            continue
        date = _within(day(match[1]), bounds)
        label = upper(match[2])
        if label == "SALDO INICIAL":
            if opening is not None or match[4]:
                fail()
            opening = running = money(match[3])
        elif label == "SALDO FINAL":
            if match[4] or money(match[3]) != running or closing is not None:
                fail()
            closing = running
            checks += 1
        else:
            if running is None or match[4] is None or closing is not None:
                fail()
            magnitude, balance = money(match[3]), money(match[4])
            delta = balance - running
            if magnitude < 0 or abs(delta) != magnitude or rows and date < rows[-1].transaction.date:
                fail()
            rows.append(_row(page, line, date, match[2], delta, balance))
            running = balance
            checks += 1
    incoming = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
    outgoing = -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    if not rows or closing is None or totals != {"CREDITOS": incoming, "DEBITOS": outgoing}:
        fail()
    return VerifiedStatement(rows, kind, opening, closing, printed_balance_checks=checks + 2)


def _alelo(pages, kind):
    text = "\n".join(pages)
    period = re.search(rf"DE:\s*({DATE})\s+ATE:\s*({DATE})", upper(text))
    if period is None:
        fail()
    bounds = day(period[1]), day(period[2])
    rows = []
    amount_pattern = re.compile(r"(-?)R\$\s*((?:\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+|\d+),\d{2})(?![\w.,])")
    pending, refs, date = [], [], None

    def finish():
        if not pending:
            return
        raw = " ".join(pending)
        values = list(amount_pattern.finditer(raw))
        if len(values) != 1 or raw.count("R$") != 1:
            fail("Não foi possível associar o valor à linha do cartão Alelo. Envie o extrato completo e legível.")
        value = values[0]
        # This portal prints both 1.000,00 and 1,000,00. Validate grouping
        # before removing separators; malformed OCR must not become money.
        integer, cents = value[2].rsplit(",", 1)
        amount = Decimal(integer.replace(",", "").replace(".", "") + "." + cents) * (-1 if value[1] else 1)
        description = (raw[:value.start()] + " " + raw[value.end():]).strip()
        if not description:
            fail()
        rows.append(replace(_row(refs[0][0], refs[0][1], date, description, amount), source_line_refs=tuple(refs)))

    for page, line, raw in _lines(pages):
        dated = re.match(r"^(\d{2}/\d{2}/\d{2})(?:\s+(.+))?$", raw)
        if dated:
            finish()
            date = _within(day(dated[1], "%d/%m/%y"), bounds)
            pending, refs = [dated[2] or ""], [(page, line)]
        elif upper(raw).startswith(("SALDO NO DIA", "ALELO - PORTAL", "EXTRATO CARTAO", "CARTAO ")):
            finish()
            pending, refs = [], []
        elif pending:
            pending.append(raw)
            refs.append((page, line))
    finish()
    if not rows or any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    rows.reverse()
    # The only balance is a current balance outside the selected period.
    return VerifiedStatement(rows, kind, None, None)


def _btg_invoice(pages, kind):
    text = upper("\n".join(pages))
    billed = re.search(r"FATURA DE [A-Z]+ DE (20\d{2})", text)
    due = re.search(r"VENCIMENTO:\s*\d{2}/(\d{2})", text)
    if billed is None or due is None:
        fail()
    year, due_month = int(billed[1]), int(due[1])
    summaries = {}
    for name, label in {"expenses": "TOTAL DE COMPRAS E DESPESAS DESSA FATURA", "opening": "TOTAL DA FATURA ANTERIOR",
                        "payments": "TOTAL DE PAGAMENTOS FEITOS PELO CLIENTE", "closing": "TOTAL A PAGAR"}.items():
        values = [money(m[1]) for m in re.finditer(re.escape(label) + rf"\s*-?R\$\s*({NUMBER})", text)]
        summaries[name] = _one(values)
    if summaries["opening"] - summaries["payments"] + summaries["expenses"] != summaries["closing"]:
        financial_mismatch()
    rows, cards, card_totals = [], {}, {}
    card = None
    pending_foreign = None
    active = False
    dated = re.compile(r"(?<!\w)(\d{2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+", re.I)
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        header = re.search(rf"LANCAMENTOS DO CARTAO .*?FINAL\s+(\d{{4}}).*?TOTAL DO CARTAO:\s*R\$\s*({NUMBER})", normalized)
        if header:
            if pending_foreign:
                fail()
            card = header[1]
            if card in card_totals and card_totals[card] != money(header[2]):
                fail()
            card_totals[card] = money(header[2])
            cards.setdefault(card, Decimal(0))
            active = True
            continue
        if normalized.startswith("PAGAMENTOS FEITOS PELO CLIENTE"):
            active = True
            card = None
            continue
        if not active:
            continue
        dates = list(dated.finditer(raw))
        for index, match in enumerate(dates):
            fragment = raw[match.end():dates[index + 1].start() if index + 1 < len(dates) else len(raw)].strip()
            value = re.match(rf"(.+?)\s+(-?\s*(?:R|US)\$)\s*({NUMBER})(?![\d,.])", fragment)
            if value is None:
                fail()
            tail = upper(fragment[value.end():])
            if tail and not tail.startswith(("COTACAO DA MOEDA", "CONVERSAO PARA REAL")):
                fail()
            month = _MONTHS[upper(match[2])]
            date = day(f"{match[1]}/{month:02d}/{year - (month > due_month)}")
            if "US$" in value[2]:
                if pending_foreign or card is None:
                    fail()
                pending_foreign = (page, line, date, value[1], card)
                continue
            amount = money(value[3])
            payment = "PAGAMENTO DE FATURA" in upper(value[1])
            if payment != (card is None):
                fail()
            amount *= 1 if payment else -1
            description = value[1] + (f" CARTAO FINAL {card}" if card else "")
            rows.append(_row(page, line, date, description, amount))
            if card:
                cards[card] += -amount
        conversion = re.search(rf"CONVERSAO PARA REAL\s*-\s*R\$\s*({NUMBER})", normalized)
        if conversion:
            if pending_foreign is None:
                fail()
            p, n, date, description, foreign_card = pending_foreign
            amount = money(conversion[1])
            row = _row(p, n, date, description + f" CARTAO FINAL {foreign_card}", -amount)
            rows.append(replace(row, source_line_refs=(*row.source_line_refs, (page, line))))
            cards[foreign_card] += amount
            pending_foreign = None
    incoming = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
    outgoing = -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    if (not rows or pending_foreign or cards != card_totals or incoming != summaries["payments"]
            or outgoing != summaries["expenses"]):
        fail()
    rows.sort(key=lambda row: row.transaction.date)
    return VerifiedStatement(rows, kind, -summaries["opening"], -summaries["closing"],
                             printed_balance_checks=4 + len(cards))
