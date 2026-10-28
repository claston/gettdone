"""Explicit signed ledgers; summary and future sections never become movements."""

import re
from dataclasses import replace
from datetime import date as calendar_date
from datetime import timedelta
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import optional_document_period, period_contains
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _lines, _row, day, fail, money
from app.application.sweep_statement_detection import BB_SISBB, CAIXA, CREDISIS, MERCADO_PAGO, SICREDI, TRIBANCO

_CURRENCY = re.compile(rf"([+-]?)\s*R\$\s*([+-]?{NUMBER})(?:\s*([CD])\b)?")
_CD = re.compile(rf"({NUMBER})\s*([CD])")


def parse_sweep_statement(pages, kind):
    parser = {
        BB_SISBB: _sisbb,
        SICREDI: _sicredi,
        TRIBANCO: _tribanco,
        MERCADO_PAGO: _mercado_pago,
        CAIXA: _caixa,
        CREDISIS: _credisis,
    }.get(kind)
    if parser is None:
        fail("Este layout exige as coordenadas do OCR. Envie o PDF completo para conferir as colunas.")
    return parser(pages, kind)


def _currency(match):
    return money(match[2].lstrip("+-")) * (-1 if match[1] == "-" or match[2].startswith("-") or match[3] == "D" else 1)


def _ordered(rows):
    if not rows or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()


def _continue(rows, page, line, raw):
    previous = rows[-1]
    rows[-1] = replace(
        previous,
        transaction=replace(previous.transaction, description=previous.transaction.description + " " + raw),
        source_line_refs=(*previous.source_line_refs, (page, line)),
    )


def _sisbb(pages, kind):
    rows, opening, running, closing, checks = [], None, None, None, 0
    blocked = 0
    prefix, prefix_refs = [], []
    for page, line, raw in _lines(pages):
        label = upper(raw)
        if label.startswith("INFORMACOES ADICIONAIS"):
            break
        dated = re.match(rf"^({DATE})\s+(.+)$", raw)
        if not dated:
            if re.match(r"^(PIX\b|TRANSFERENCIA\b|TED\b|DEP CHEQUE\b)", label):
                prefix.append(raw)
                prefix_refs.append((page, line))
            elif rows and not label.startswith(("SISBB", "AGENCIA:", "DATA MOVIM.", "INFORMACOES")):
                _continue(rows, page, line, raw)
            continue
        date, rest = day(dated[1]), dated[2]
        if "DEPOSITO BLOQUEAD." in upper(rest) and re.search(rf"{NUMBER}\s*\*$", rest):
            blocked += 1
            prefix, prefix_refs = [], []
            continue
        cells = list(_CD.finditer(rest))
        if not cells or len(cells) > 2 or rest[cells[-1].end() :].strip():
            fail()
        values = [money(m[1]) * (-1 if m[2] == "D" else 1) for m in cells]
        description = rest[: cells[0].start()].strip()
        compact = upper(description).replace(" ", "")
        if "SALDOANTERIOR" in compact:
            if opening is not None or rows or len(set(values)) != 1:
                fail()
            opening = running = values[0]
        elif compact == "SALDO":
            if running is None or values != [running] or closing is not None:
                fail()
            closing = running
            checks += 1
        else:
            if running is None or closing is not None:
                fail()
            reference = re.search(r"\s+(\d[\d.]*)$", description)
            if reference is None:
                fail()
            description = description[: reference.start()].strip()
            description = re.sub(r"^\d{1,4}-[\dXx]\s+", "", description)
            running += values[0]
            balance = values[1] if len(values) == 2 else None
            if balance is not None:
                if running != balance:
                    financial_mismatch()
                checks += 1
            description = " ".join([*prefix, description])
            row = _row(page, line, date, description, values[0], balance, reference[1])
            rows.append(replace(row, source_line_refs=(*prefix_refs, *row.source_line_refs)))
            prefix, prefix_refs = [], []
    _ordered(rows)
    if closing is None or not checks:
        fail()
    return VerifiedStatement(rows, kind, opening, closing, printed_balance_checks=checks, non_cash_entries=blocked)


def _sicredi(pages, kind):
    text = "\n".join(pages)
    period_pattern = rf"PERIODO DE\s+({DATE})\s+A\s+({DATE})"
    bounds = re.search(period_pattern, upper(text))
    for field in (r"COOPERATIVA:\s*([\d.-]+)", r"CONTA:\s*([\d.-]+)"):
        if len(set(re.findall(field, upper(text)))) > 1:
            fail("Há contas diferentes no mesmo arquivo Sicredi. Envie um extrato por conta.")
    start, end = optional_document_period(bounds)
    rows, opening, running, checks = [], None, None, 0
    period_seen, period_opened, future = False, False, False
    for page, line, raw in _lines(pages):
        label = upper(raw)
        period = re.search(period_pattern, label)
        if period:
            new_start, new_end = day(period[1]), day(period[2])
            if new_start > new_end:
                fail()
            if period_seen and (new_start, new_end) != (start, end):
                if not period_opened or new_start != (calendar_date.fromisoformat(end) + timedelta(days=1)).isoformat():
                    fail("Os períodos do Sicredi não são consecutivos. Envie o extrato completo da mesma conta.")
                if not re.search(r"CONTA:\s*[\d.-]+", upper(text)):
                    fail("Não foi possível confirmar a conta dos períodos Sicredi. Envie um arquivo por conta.")
                period_opened = False
            start, end, period_seen, future = new_start, new_end, True, False
        if label.startswith("LANCAMENTOS FUTUROS"):
            future = True
        if future:
            continue
        if label.startswith("SALDO ANTERIOR"):
            values = re.findall(rf"[+-]?{NUMBER}", raw)
            if len(values) != 1 or period_opened:
                fail()
            balance = money(values[0])
            if opening is None:
                opening = running = balance
            elif running != balance:
                financial_mismatch()
            period_opened = True
        dated = re.match(rf"^({DATE})\s+(.+?)\s+([+-]?{NUMBER})\s+([+-]?{NUMBER})$", raw)
        if not dated:
            if re.match(rf"^{DATE}\b", raw):
                fail()
            continue
        date, description, amount, balance = day(dated[1]), dated[2], money(dated[3]), money(dated[4])
        if not period_opened or running is None or not period_contains(date, start, end):
            fail()
        if running + amount != balance:
            financial_mismatch()
        # An overlapping text object repeats a prefix, not the transaction.
        for size in range(len(description) // 2, 8, -1):
            prefix = description[:size]
            if description.startswith(prefix + " " + prefix):
                description = description[size + 1 :]
                break
        running = balance
        rows.append(_row(page, line, date, description, amount, balance))
        checks += 1
    if not period_opened:
        fail()
    _ordered(rows)
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=checks)


def _tribanco(pages, kind):
    rows, opening, running, current, checks = [], None, None, None, 0
    last_control = False
    for page, line, raw in _lines(pages):
        if upper(raw).startswith(("LANCAMENTOS FUTUROS", "POSICAO DA CONTA")):
            break
        dated = re.match(rf"^({DATE})\s*(.*)$", raw)
        rest = raw
        if dated:
            current, rest = day(dated[1]), dated[2]
        label = upper(rest)
        values = list(_CURRENCY.finditer(rest))
        if label.startswith("SALDO ANTERIOR"):
            if opening is not None or len(values) != 1 or rows:
                fail()
            opening = running = _currency(values[0])
            continue
        if label.startswith("SALDO C/C"):
            if len(values) != 1 or running != _currency(values[0]):
                fail()
            checks += 1
            last_control = True
            continue
        direction = re.search(r"(Entrada|Saída)\s*(\d+)?\s*([+-])\s*R\$", rest, re.I)
        if direction is None:
            if dated and values or re.search(r"[+-]\s*R\$", rest):
                fail()
            continue
        if len(values) != 1 or current is None or running is None:
            fail()
        amount = _currency(values[0])
        if (upper(direction[1]) == "SAIDA") != (direction[3] == "-"):
            fail()
        description = rest[: direction.start()].strip()
        if not description:
            fail()
        running += amount
        rows.append(_row(page, line, current, description, amount, reference=direction[2]))
        last_control = False
    _ordered(rows)
    if not checks or not last_control:
        fail()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=checks)


def _mercado_pago(pages, kind):
    text = upper("\n".join(pages))
    period = re.search(r"DE (\d{2}-\d{2}-\d{4}) AL (\d{2}-\d{2}-\d{4})", text)
    start, end = optional_document_period(period, parse_date=lambda value: day(value, "%d-%m-%Y"))
    summary = {}
    for key, label in {"opening": "SALDO INICIAL:", "closing": "SALDO FINAL:", "credit": "ENTRADAS:", "debit": "SAIDAS:"}.items():
        values = [
            _currency(m) for m in _CURRENCY.finditer(text) if text[max(0, m.start() - len(label) - 2) : m.start()].strip().endswith(label)
        ]
        if len(values) != 1:
            fail()
        summary[key] = values[0]
    rows, running, references = [], summary["opening"], {}
    prefix, prefix_refs = [], []
    description_columns = {}
    for page, content in enumerate(pages, 1):
        for raw in content.splitlines():
            match = re.search(r"Descri[cç][aã]o", raw, re.I)
            if match and "ID DA OPERACAO" in upper(raw):
                description_columns[page] = match.start()
                break
    active = True
    for page, line, raw in _lines(pages):
        dated = re.match(r"^(\d{2}-\d{2}-\d{4})\s+(.+)$", raw)
        if not dated:
            label = upper(raw)
            if label.startswith("DATA DE GERACAO:"):
                active = False
            if not active:
                continue
            if re.match(r"^(PIX (?:RECEBIDO|ENVIADO)\b|REEMBOLSO DE PIX ENVIADO\b|PAGAMENTO (?:DE CONTA|COM QR PIX)\b)", label):
                prefix.append(raw)
                prefix_refs.append((page, line))
            elif rows and page in description_columns and not re.fullmatch(r"\d+/\d+", raw):
                physical = pages[page - 1].splitlines()[line - 1]
                indent = len(physical) - len(physical.lstrip())
                if description_columns[page] - 1 <= indent <= description_columns[page] + 3:
                    _continue(rows, page, line, raw)
            continue
        values = list(_CURRENCY.finditer(dated[2]))
        if len(values) != 2:
            fail()
        date = day(dated[1], "%d-%m-%Y")
        description = dated[2][: values[0].start()].strip()
        reference = re.search(r"\s+(\d+)$", description)
        if not reference or not period_contains(date, start, end):
            fail()
        amount, balance = map(_currency, values)
        description = " ".join([*prefix, description[: reference.start()].strip()])
        previous = references.get(reference[1], [])
        shared_reference = False
        if previous:
            original = rows[previous[0]]
            shared_reference = (len(previous) != 1 or not re.match(r"^PIX ENVIADO\b", upper(original.transaction.description))
                    or not re.match(r"^REEMBOLSO DE PIX ENVIADO\b", upper(description))
                    or original.transaction.amount >= 0 or amount != -Decimal(str(original.transaction.amount))
                    or day(original.transaction.date, "%Y-%m-%d") > date)
            if shared_reference:
                for index in previous:
                    earlier = rows[index]
                    rows[index] = replace(earlier, warnings=list(dict.fromkeys(
                        [*earlier.warnings, "manual_review_recommended", "shared_operation_reference"])))
        running += amount
        if running != balance:
            financial_mismatch()
        row = _row(page, line, date, description, amount, balance, reference[1])
        if shared_reference:
            row = replace(row, warnings=["manual_review_recommended", "shared_operation_reference"])
        rows.append(replace(row, source_line_refs=(*prefix_refs, *row.source_line_refs)))
        references.setdefault(reference[1], []).append(len(rows) - 1)
        prefix, prefix_refs = [], []
    _ordered(rows)
    credits = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
    debits = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    if running != summary["closing"] or credits != summary["credit"] or debits != summary["debit"]:
        financial_mismatch()
    return VerifiedStatement(rows, kind, summary["opening"], running, printed_balance_checks=len(rows) + 3)


def _caixa(pages, kind):
    # This browser print has an expanded text map: native content-stream order
    # keeps every glyph, whereas fixed-width extraction fragments the numbers.
    text = "\n".join(pages)
    opening_match = re.search(rf"SALDO ANTERIOR AO PERIODO SOLICITADO\s+R\$\s*({NUMBER})\s*([CD])", upper(text))
    if not opening_match:
        fail()
    opening = running = money(opening_match[1]) * (-1 if opening_match[2] == "D" else 1)
    rows, pending, refs, current, checks = [], [], [], None, 0
    for page, line, raw in _lines(pages):
        dated = re.match(rf"^({DATE})(?:\s+(.*))?$", raw)
        if dated:
            if pending:
                fail()
            current, pending, refs = day(dated[1]), [dated[2] or ""], [(page, line)]
        elif pending:
            pending.append(raw)
            refs.append((page, line))
        else:
            continue
        joined = " ".join(pending)
        values = list(_CURRENCY.finditer(joined))
        if not values:
            continue
        if upper(joined).startswith("SALDO DIA"):
            if len(values) != 1 or _currency(values[0]) != running:
                fail()
            checks += 1
        else:
            if len(values) != 2 or joined[values[-1].end() :].strip():
                fail()
            amount, balance = map(_currency, values)
            running += amount
            if running != balance:
                financial_mismatch()
            description = joined[: values[0].start()].strip()
            rows.append(replace(_row(page, line, current, description, amount, balance), source_line_refs=tuple(refs)))
            checks += 1
        pending, refs = [], []
    _ordered(rows)
    if pending or not checks:
        fail()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=checks)


def _credisis(pages, kind):
    # Pending deposits have no available cash effect. Keep the count visible;
    # only their later release enters the signed cash ledger.
    rows, opening, closing, blocked = [], None, None, 0
    active = True
    for page, line, raw in _lines(pages):
        label = upper(raw)
        if "PAGINA " in label or re.match(r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", raw):
            continue
        if label.startswith("SALDOS EM "):
            active = False
        if not active:
            continue
        if label.startswith(("SALDO FINAL", "SALDO ANTERIOR")):
            values = list(_CURRENCY.finditer(raw))
            if len(values) != 1:
                fail()
            if label.startswith("SALDO FINAL"):
                closing = _currency(values[0])
            else:
                opening = _currency(values[0])
            continue
        dated = re.match(rf"^({DATE})\s+(.+)$", raw)
        if not dated:
            continue
        values = list(_CURRENCY.finditer(dated[2]))
        if len(values) != 2:
            fail()
        date = day(dated[1])
        description = dated[2][: values[0].start()].strip()
        amount, balance = map(_currency, values)
        if "DEPOSITO BLOQ" in upper(description):
            blocked += 1
            rows.append(_row(page, line, date, description, Decimal(0), balance))
        else:
            rows.append(_row(page, line, date, description, amount, balance))
    if opening is None or closing is None:
        fail()
    rows.reverse()
    _ordered(rows)
    running = opening
    for row in rows:
        running += Decimal(str(row.transaction.amount))
        if running != Decimal(str(row.running_balance)):
            financial_mismatch()
    if running != closing:
        financial_mismatch()
    return VerifiedStatement(
        [r for r in rows if r.transaction.amount != 0], kind, opening, closing, printed_balance_checks=len(rows), non_cash_entries=blocked
    )
