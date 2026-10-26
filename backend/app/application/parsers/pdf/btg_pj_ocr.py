"""BTG PJ descending ledger: read roles by geometry, preserve inconsistent source balances."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions

LAYOUT = "btg_pactual_conta_corrente_pj_v1"


def parse_btg_pj(extraction, *, native=False):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    if ("CONTACORRENTEPJ" not in re.sub(r"[\s-]+", "", text) or "ENTRADAS / SAIDAS (R$)" not in text
            or not re.search(r"\bBTG\b|\bBTGPACTUAL\.COM\b", text)):
        return None
    rows, balances, totals = [], {}, {"TOTAL DE ENTRADAS": set(), "TOTAL DE SAIDAS": set()}
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        labels = {upper(line.text): line for line in lines}
        amount_header = labels.get("ENTRADAS / SAIDAS (R$)")
        balance_header = labels.get("SALDO (R$)")
        date_header = labels.get("DATA LANCAMENTO")
        history_header = next((line for label, line in labels.items() if label.startswith("DESCRICAO DO LANCAMENTO")), None)
        if any(value is None for value in (amount_header, balance_header, date_header, history_header)):
            fail()
        dx, hx, ax, bx = [line.bbox["left"] for line in (date_header, history_header, amount_header, balance_header)]
        if not dx < hx < ax < bx:
            fail()
        header_y = max(line.bbox["top"] for line in (date_header, history_header, amount_header, balance_header))
        dates = sorted([line for line in lines if re.fullmatch(r"\d{2}/\d{2}/\d{4}", line.text)
                        and line.bbox["left"] < (dx+hx)/2 and line.bbox["top"] > header_y], key=lambda line: line.bbox["top"])
        amounts = [line for line in lines if re.fullmatch(rf"-?{NUMBER}", line.text) and line.bbox["left"] >= ax-.025
                   and line.bbox["top"] > header_y]
        used = set()
        for i, date in enumerate(dates):
            y = date.bbox["top"]
            lower = (dates[i-1].bbox["top"]+y)/2 if i else header_y+.01
            higher = (y+dates[i+1].bbox["top"])/2 if i+1 < len(dates) else y+.018
            descriptions = sorted([line for line in lines if hx-.02 <= line.bbox["left"] < ax-.025
                                   and lower <= line.bbox["top"] < higher], key=lambda line: line.bbox["top"])
            description = " ".join(line.text for line in descriptions)
            values = [line for line in amounts if abs(line.bbox["top"]-y) < .006]
            movement = [line for line in values if line.bbox["left"] < (ax+bx)/2]
            balance = [line for line in values if line.bbox["left"] >= (ax+bx)/2]
            if not description or len(balance) != 1:
                fail()
            used.update(line.id for line in values)
            label = upper(description)
            if label in {"SALDO DE ABERTURA", "SALDO DE FECHAMENTO"}:
                if movement or label in balances:
                    fail()
                balances[label] = money(balance[0].text)
                continue
            if len(movement) != 1:
                fail()
            row = _row(page.page_number, date.line_index, day(date.text), description, money(movement[0].text), money(balance[0].text))
            rows.append(replace(row, source_regions=evidence_regions(page.page_number, [date, *descriptions, *values], words=page.words)))
        if any(line.id not in used for line in amounts):
            fail()
        for label in totals:
            for anchor in [line for line in lines if upper(line.text) == label]:
                candidates = [line for line in lines if line.bbox["left"] > anchor.bbox["left"]+.15
                              and abs(line.bbox["top"]-anchor.bbox["top"]) < .006
                              and re.fullmatch(rf"(?:R\$\s*)?{NUMBER}", line.text)]
                if len(candidates) == 1:
                    totals[label].add(money(candidates[0].text.removeprefix("R$").strip()))
    if not rows or set(balances) != {"SALDO DE ABERTURA", "SALDO DE FECHAMENTO"}:
        fail()
    dates = [row.transaction.date for row in rows]
    ascending = native and dates == sorted(dates)
    if not ascending and dates != sorted(dates, reverse=True):
        fail()
    incoming = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    outgoing = sum((-Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if totals != {"TOTAL DE ENTRADAS": {incoming}, "TOTAL DE SAIDAS": {outgoing}}:
        fail("Os lançamentos extraídos não correspondem aos totais impressos do BTG. Envie o extrato original completo.")
    if not ascending:
        rows.reverse()  # Preserve accounting order within the descending dates as well.
    if balances["SALDO DE ABERTURA"] + incoming - outgoing != balances["SALDO DE FECHAMENTO"]:
        rows[-1] = replace(rows[-1], warnings=[*rows[-1].warnings, "printed_summary_balance_mismatch"])
    return VerifiedStatement(rows, LAYOUT, balances["SALDO DE ABERTURA"], balances["SALDO DE FECHAMENTO"])
