"""Dated app cards with explicit C/D movements and separately labelled balances."""

import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "caixa_app_signed_balance_cards_v1"
_FURNITURE = {"EXTRATO", "GERAR ARQUIVO PDF", "VOLTAR", "EXTRATO POR PERIODO"}


def is_signed_balance_cards(text):
    lines = [upper(line) for line in text.splitlines() if line.strip()]
    return (bool(lines) and lines[0] == "EXTRATO" and _FURNITURE.issubset(lines)
            and any(line.startswith("SALDO DIA ") for line in lines)
            and sum(bool(re.fullmatch(rf"SALDO {NUMBER} [CD]", line)) for line in lines) >= 2)


def parse_signed_balance_cards(pages):
    lines = [(page, number, line.strip()) for page, content in enumerate(pages, 1)
             for number, line in enumerate(content.splitlines(), 1) if line.strip() and upper(line) not in _FURNITURE]
    if len(lines) % 3:
        fail("Um lançamento do aplicativo está sem data, referência, valor ou saldo legível.")
    rows, running, checks, previous_date = [], None, 0, None
    for index in range(0, len(lines), 3):
        header, amount_line, balance_line = lines[index:index+3]
        anchor = re.fullmatch(rf"(.+?)\s+({DATE})", header[2])
        amount = re.fullmatch(rf"(\d{{6}})\s+({NUMBER})\s+([CD])", upper(amount_line[2]))
        balance = re.fullmatch(rf"SALDO\s+({NUMBER})\s+([CD])", upper(balance_line[2]))
        if not anchor or not amount or not balance:
            fail("Um lançamento do aplicativo está sem data, referência, valor ou indicador C/D legível.")
        date = day(anchor[2])
        if previous_date and date < previous_date:
            fail("A ordem das datas dos movimentos do aplicativo não pode ser confirmada.")
        previous_date = date
        value = money(amount[2]) * (-1 if amount[3] == "D" else 1)
        printed = money(balance[1]) * (-1 if balance[2] == "D" else 1)
        if upper(anchor[1]) == "SALDO DIA":
            if amount[1] != "000000" or value != printed:
                fail("Os dois controles diários impressos no aplicativo não conferem.")
            if running is not None and printed != running:
                financial_mismatch()
        else:
            if running is not None and running + value != printed:
                financial_mismatch()
            row = _row(header[0], header[1], date, anchor[1], value, printed, amount[1])
            rows.append(replace(row, source_line_refs=tuple((item[0], item[1]) for item in (header, amount_line, balance_line))))
        running = printed
        checks += 1
    if not rows:
        fail("O documento não contém movimentos legíveis.")
    # The first transaction balance is not an opening balance. Do not derive
    # a missing boundary from arithmetic, or claim complete global coverage.
    return VerifiedStatement(rows, LAYOUT, None, running, printed_balance_checks=checks,
                             candidate_rows=len(rows), balance_bounds_unavailable=True)
