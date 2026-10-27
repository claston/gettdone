"""Signed exports with explicit date conventions and non-cash position roles."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _lines, _row, day, fail, money

BB_ENGLISH = "bb_english_bank_statement_v1"
ITAU = "itau_empresas_cash_combined_positions_v1"
BRB = "brb_monthly_displaced_history_v1"
MONTHS = "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split()
US_MONEY = r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"


def identify_final_native(text):
    label = upper(text)
    compact = label.replace(" ", "")
    if "BANK STATEMENT" in label and re.search(r"BANK ID\s+001\b", label) and all(
        key in label for key in ("CHECKING", "BRL", "TRANSACTION DETAILS", "TOTAL CREDITS", "TOTAL DEBITS")
    ):
        return BB_ENGLISH
    if "ITAUEMPRESAS" in compact and "AG./ORIGEM" in compact and "SDO CTA/APL AUTOMATICAS" in label:
        return ITAU
    if "SALDODAPOUPANCA/CDBAUTOMATICO/FUNDOAUTOMATICO" in compact and all(
        key in compact for key in ("DIAHISTORICOVALOR", "CONTACORRENTE", "DOC:")
    ):
        return BRB
    return None


def parse_final_native(pages, kind):
    return {BB_ENGLISH: _english, ITAU: _itau, BRB: _brb}[kind](pages, kind)


def _english(pages, kind):
    text = "\n".join(pages)
    period = re.search(rf"({DATE})\s*-\s*({DATE})", text)
    expected = re.search(r"Transaction Details\s*\((\d+) transactions\)", text, re.I)
    if not period or not expected:
        fail()
    start, end = [day(value, "%m/%d/%Y") for value in period.groups()]
    rows = []
    for page, line, raw in _lines(pages):
        if re.fullmatch(rf"{DATE}\s*-\s*{DATE}", raw):
            continue
        if not re.match(rf"^{DATE}\b", raw):
            continue
        match = re.fullmatch(rf"({DATE})\s+(DEP|DEBIT)\s+(.+?)\s+(-?)R\$\s*({US_MONEY})", raw)
        if not match:
            fail("Uma linha do extrato em inglês está incompleta ou contém um tipo não reconhecido.")
        date = day(match[1], "%m/%d/%Y")
        if not start <= date <= end or (match[2] == "DEBIT") != bool(match[4]):
            fail()
        amount = Decimal(match[5].replace(",", "")) * (-1 if match[4] else 1)
        rows.append(_row(page, line, date, match[3], amount))
    credits = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount >= 0), Decimal(0))
    debits = -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    def header(label):
        values = re.findall(re.escape(label) + rf"\s*R\$\s*({US_MONEY})", text, re.I)
        if len(values) != 1:
            fail()
        return Decimal(values[0].replace(",", ""))
    totals = re.findall(rf"TOTAL\s+\+R\$({US_MONEY})\s+-R\$({US_MONEY})", text)
    if len(rows) != int(expected[1]) or not rows or credits != header("Total Credits") or debits != header("Total Debits"):
        fail("A contagem ou os totais do extrato em inglês não correspondem aos movimentos.")
    if totals != [(format(credits, ",.2f"), format(debits, ",.2f"))]:
        fail()
    closing = header("Current Balance")
    rows.sort(key=lambda r: r.transaction.date)
    opening = closing - credits + debits
    return VerifiedStatement(rows, kind, opening, closing, opening_inferred=True, candidate_rows=int(expected[1]))


def _short_date(raw, start, end):
    choices = [day(f"{raw}/{year}") for year in range(int(start[:4]), int(end[:4]) + 1)]
    choices = [value for value in choices if start <= value <= end]
    if len(choices) != 1:
        fail("Uma data de movimento está fora do período impresso.")
    return choices[0]


def _itau(pages, kind):
    period = re.search(rf"EXTRATO DE\s+({DATE})\s+ATE\s+({DATE})", upper("\n".join(pages)))
    if not period:
        fail()
    start, end = day(period[1]), day(period[2])
    rows, positions = [], 0
    for page, line, raw in _lines(pages):
        if not re.match(r"^\d{2}/\d{2}\b", raw):
            continue
        label = upper(raw)
        if any(key in label for key in ("SDO CTA/APL AUTOMATICAS", "SALDO ANTERIOR", "S A L D O")):
            if not re.search(rf"{NUMBER}$", raw):
                fail()
            positions += 1
            continue
        match = re.fullmatch(rf"(\d{{2}}/\d{{2}})\s+(.+?)(?:\s+(\d{{4}}))?\s+(-?{NUMBER})", raw)
        if not match:
            fail()
        rows.append(_row(page, line, _short_date(match[1], start, end), match[2], money(match[4]), reference=match[3]))
    if not rows or not positions:
        fail()
    rows[0] = replace(rows[0], warnings=["combined_cash_investment_balance_not_comparable"])
    return VerifiedStatement(rows, kind, None, None)


def _brb(pages, kind):
    month = re.search(r"\b(" + "|".join(MONTHS) + r")\s*/\s*(\d{4})\b", upper("\n".join(pages)))
    if not month:
        fail()
    month_number, year = MONTHS.index(month[1]) + 1, int(month[2])
    rows = []
    for page, content in enumerate(pages, 1):
        lines = [(n, value.strip()) for n, value in enumerate(content.splitlines(), 1) if value.strip()]
        histories = [(n, value) for n, value in lines if re.fullmatch(r".+\s+[–—-]\s*DOC:\s*\d+", value)]
        dates = [(n, value) for n, value in lines if re.match(r"^\d{2}/\d{2}(?=\s)", value)]
        if len(histories) != len(dates) or not dates:
            fail("Não foi possível associar todos os históricos aos valores do extrato BRB.")
        for (history_line, history), (line, raw) in zip(histories, dates):
            match = re.fullmatch(rf"(\d{{2}}/\d{{2}})\s+(?:(.+?)\s+)?R\$\s*([+−-])\s*({NUMBER})", raw)
            if not match or int(match[1][3:]) != month_number:
                fail()
            date = day(f"{match[1]}/{year}")
            amount = money(match[4]) * (-1 if match[3] in "−-" else 1)
            reference = re.search(r"DOC:\s*(\d+)", history)[1]
            description = f"{history} | {match[2]}" if match[2] else history
            row = _row(page, line, date, description, amount, reference=reference)
            rows.append(replace(row, source_line_refs=((page, history_line), (page, line))))
    if any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    rows.reverse()
    return VerifiedStatement(rows, kind, None, None)
