"""Explicit period scopes and signed monetary columns precede generic heuristics."""

import re
from datetime import date
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

SAFRA = "banco_safra_extrato_movimentacao_lancamentos_realizados_v1"
C6 = "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1"


def identify_period_signed_table(text):
    text = upper(text)
    if "BANCO SAFRA" in text and "LANCAMENTOS REALIZADOS" in text and "VALOR (R$)" in text:
        return SAFRA
    if ("C6" in text or "CHEQUE ESPECIAL CONTRATADO" in text) and all(
        s in text for s in ("CONTABIL", "ENTRADAS:", "SAIDAS:", "SALDO DO DIA")
    ):
        return C6
    return None


def _date_in_period(short, start, end):
    candidates = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        try:
            value = date(year, int(short[3:]), int(short[:2])).isoformat()
        except ValueError:
            continue
        if start <= value <= end:
            candidates.append(value)
    if len(candidates) != 1:
        fail("A data do movimento não tem um único ano confirmado pelo período impresso.")
    return candidates[0]


def _period(a, b):
    start, end = day(a), day(b)
    if start > end:
        fail("O período impresso tem início posterior ao fim.")
    return start, end


def parse_period_signed_table(pages, kind):
    return _safra(pages) if kind == SAFRA else _c6(pages)


def _safra(pages):
    rows, pending, period = [], None, None
    checks = 0
    previous_balance = None
    since_control = Decimal(0)

    def flush():
        nonlocal pending, previous_balance, since_control, checks
        if pending is None:
            return
        page, line, short, pieces = pending
        if period is None:
            fail("Falta o período necessário para interpretar o ano dos movimentos.")
        dated = _date_in_period(short, *period)
        body = " ".join(pieces)
        values = list(re.finditer(rf"(?<![\d.,])([+-]?{NUMBER})(?![\d.,])", body))
        if len(values) != 1:
            fail("Não foi possível associar um único valor à linha da tabela de movimentos.")
        value = money(values[0][1])
        description = body[: values[0].start()].strip()
        if description.startswith("SALDO TOTAL"):
            if previous_balance is not None:
                if previous_balance + since_control != value:
                    financial_mismatch("Os saldos diários não conferem com os movimentos impressos.")
                checks += 1
            previous_balance, since_control = value, Decimal(0)
        else:
            if not description or "SALDO" in description:
                fail("Uma linha da tabela não possui papel financeiro inequívoco.")
            rows.append(_row(page, line, dated, description, value))
            since_control += value
        pending = None

    for page, text in enumerate(pages, 1):
        for line, raw in enumerate(text.splitlines(), 1):
            normalized = upper(raw)
            match = re.search(rf"PERIODO DE\s+({DATE})\s+A\s+({DATE})", normalized)
            if match:
                flush()
                scope = _period(*match.groups())
                if period and period != scope:
                    fail("Os períodos independentes precisam ser separados antes da leitura da tabela.")
                period = scope
                continue
            if re.match(r"^\d{2}/\d{2}/\d{4}\s+\d{2}:", normalized):
                continue
            match = re.match(r"^(\d{2}/\d{2})\s+(.+)", normalized)
            if match:
                flush()
                pending = (page, line, match[1], [match[2]])
            elif (
                pending
                and normalized
                and not re.match(r"BANCO SAFRA|PAGINA |CNPJ:|EXTRATO |LAN[CÇ]AMENTOS REALIZADOS|DATA\s+LAN|SALDO \+|R\$", normalized)
            ):
                # Only descriptions/identifiers may wrap. Page headers cannot supply a value.
                if not re.search(r"\b(?:AG:|CONTA:|LIMITE CHEQUE|COBRANCA D[01])", normalized):
                    pending[3].append(normalized)
    flush()
    if not rows:
        fail()
    return VerifiedStatement(
        rows, SAFRA, None, None, printed_balance_checks=checks, candidate_rows=len(rows), balance_bounds_unavailable=True
    )


def _c6(pages):
    rows, scope, pending = [], None, None
    month_rows, totals, checks = [], {}, 0
    previous_balance = None
    since_balance = Decimal(0)
    waiting_balance = False

    def flush():
        nonlocal pending, since_balance
        if pending is None:
            return
        page, line, dates, parts = pending
        body = " ".join(parts)
        amounts = list(re.finditer(rf"(?P<sign>[+-]?)\s*R\$\s*(?P<inner>[+-]?)(?P<value>{NUMBER})(?!\d)", body))
        if len(dates) != 2 or len(amounts) != 1 or scope is None:
            fail("Uma linha do extrato está sem as datas ou o valor monetário legível.")
        match = amounts[0]
        if match["sign"] and match["inner"] and match["sign"] != match["inner"]:
            fail("O movimento possui sinais conflitantes.")
        amount = money(match["value"]) * (-1 if "-" in (match["sign"], match["inner"]) else 1)
        movement = _date_in_period(dates[0], *scope)
        _date_in_period(dates[1], *scope)
        description = body[: match.start()].strip()
        if not description:
            fail()
        rows.append(_row(page, line, movement, description, amount))
        month_rows.append(amount)
        since_balance += amount
        pending = None

    def finish_month():
        nonlocal checks
        if scope is None:
            return
        if set(totals) != {"ENTRADAS", "SAIDAS"}:
            fail("Faltam os totais do bloco mensal.")
        expected = {
            "ENTRADAS": sum((v for v in month_rows if v > 0), Decimal(0)),
            "SAIDAS": sum((-v for v in month_rows if v < 0), Decimal(0)),
        }
        for key in expected:
            if expected[key] != totals[key]:
                financial_mismatch("O total mensal difere dos movimentos extraídos.")
            checks += 1

    for page, text in enumerate(pages, 1):
        for line, raw in enumerate(text.splitlines(), 1):
            normalized = upper(raw)
            month = re.search(rf"\b\w+\s+20\d{{2}}\s*\(\s*({DATE})\s+(?:[-–]\s*)?({DATE})\s*\)", normalized)
            if month:
                if waiting_balance:
                    fail("O saldo diário não está legível.")
                flush()
                finish_month()
                scope = _period(*month.groups())
                month_rows, totals = [], {}
                for total in re.finditer(rf"(ENTRADAS|SAIDAS):\s*R\$\s*({NUMBER})", normalized[month.end():]):
                    if total[1] in totals:
                        fail("Há totais mensais repetidos sem separação de bloco.")
                    totals[total[1]] = money(total[2])
                continue
            printed_totals = list(re.finditer(rf"(ENTRADAS|SAIDAS):\s*R\$\s*({NUMBER})", normalized))
            if printed_totals and scope:
                for total in printed_totals:
                    if total[1] in totals:
                        fail("Há totais mensais repetidos sem separação de bloco.")
                    totals[total[1]] = money(total[2])
                continue
            if normalized.startswith("SALDO DO DIA"):
                flush()
                waiting_balance = scope is not None
                inline = re.search(rf"R\$\s*([+-]?{NUMBER})", normalized)
                if not inline:
                    continue
                normalized = "R$ " + inline[1]
            if waiting_balance:
                balance = re.fullmatch(rf"R\$\s*([+-]?{NUMBER})", normalized)
                if balance:
                    value = money(balance[1])
                    if previous_balance is not None:
                        if previous_balance + since_balance != value:
                            financial_mismatch("O saldo diário difere dos movimentos do extrato.")
                        checks += 1
                    previous_balance, since_balance, waiting_balance = value, Decimal(0), False
                    continue
                if normalized:
                    fail("O saldo diário não está legível.")
            if normalized.startswith(("INFORMACOES SUJEITAS", "ATENDIMENTO 24", "OUVIDORIA")):
                flush()
                continue
            inline_row = re.fullmatch(r"(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+)", normalized)
            if inline_row and scope:
                flush()
                pending = (page, line, [inline_row[1], inline_row[2]], [inline_row[3]])
                if "R$" in inline_row[3]:
                    flush()
                continue
            dates = re.fullmatch(r"(\d{2}/\d{2})(?:\s+(\d{2}/\d{2}))?", normalized)
            if dates and scope:
                values = [v for v in dates.groups() if v]
                if pending and len(pending[2]) == 1 and not pending[3]:
                    pending[2].extend(values)
                else:
                    flush()
                    pending = (page, line, values, [])
                continue
            if pending and normalized:
                pending[3].append(normalized)
                if "R$" in normalized:
                    flush()
    flush()
    if waiting_balance:
        fail("O saldo diário não está legível.")
    finish_month()
    if not rows:
        fail()
    return VerifiedStatement(rows, C6, None, None, printed_balance_checks=checks, candidate_rows=len(rows), balance_bounds_unavailable=True)
