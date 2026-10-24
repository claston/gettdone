"""XP brokerage account ledger; distinguish transaction/settlement and forecasts."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event, date, money
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, fail

EVIDENCE = "xp_investment_account_ledger"
CURRENCY = re.compile(rf"([+-]?)\s*R\$\s*([+-]?{NUMBER})")


def _amount(match):
    raw = match[1] + match[2]
    if raw.count("-") + raw.count("+") > 1:
        fail("Um valor do extrato XP tem sinais incompatíveis.")
    return money(raw)


def parse_xp_investment_pages(pages):
    text = "\n".join(pages)
    normalized = upper(text)
    period = re.search(r"DATA DO EXTRATO:\s*(\d{2})/(\d{4})", normalized)
    opening_match = re.search(rf"SALDO ANTERIOR\s*\(({DATE})\)\s*({CURRENCY.pattern})", normalized)
    closing_match = re.search(rf"SALDO ATUAL\s*\(({DATE})\)\s*({CURRENCY.pattern})", normalized)
    if not period or not opening_match or not closing_match:
        fail("Falta o período ou os saldos inicial/final do extrato de investimentos XP.")
    opening_date, end = date(opening_match[1]), date(closing_match[1])
    opening = Decimal(_amount(CURRENCY.search(opening_match[2])))
    closing = Decimal(_amount(CURRENCY.search(closing_match[2])))
    records, observations = [], []
    for number, page in enumerate(pages, 1):
        pending = []
        refs = []

        def flush():
            if not pending:
                return
            raw = " ".join(pending)
            row = re.match(rf"^({DATE})\s+({DATE})\s+(.+)$", raw)
            amounts = list(CURRENCY.finditer(row[3])) if row else []
            if not row or len(amounts) != 2 or row[3][amounts[-1].end():].strip():
                fail("Uma linha XP está sem datas, valor ou saldo legíveis.")
            description = row[3][:amounts[0].start()].strip()
            if not description:
                fail("Uma linha XP está sem histórico legível.")
            records.append({"dates": {"transaction": date(row[1]), "settlement": date(row[2])},
                            "description": description, "amount": _amount(amounts[0]), "balance": _amount(amounts[1]),
                            "refs": list(refs)})
            pending.clear()
            refs.clear()

        for line_number, raw in enumerate(page.splitlines(), 1):
            line = " ".join(raw.split())
            label = upper(line)
            if not line:
                continue
            ref = {"page": number, "line": line_number}
            if label.startswith(("XP INVESTIMENTOS", "PARA INFORMACOES", "GERADO EM:", "ACESSAR", "WWW.XPI.COM.BR")):
                flush()
                continue
            if re.match(rf"^{DATE}\s+{DATE}(?:\s|$)", line):
                flush()
                pending.append(line)
                refs.append(ref)
            elif pending:
                if re.match(rf"^{DATE}\b", line):
                    fail("Uma linha XP está sem suas duas datas legíveis.")
                pending.append(line)
                refs.append(ref)
            elif CURRENCY.search(line):
                amount = CURRENCY.search(line)
                projected = label.startswith(("LANCAMENTOS FUTUROS", "SALDO TOTAL PROJETADO")) or bool(re.match(rf"^{DATE}\s+\(D", line))
                observations.append({"observation_id": f"position_p{number}_l{line_number}",
                    "observation_type": "account_projection" if projected else "cash_position",
                    "description": line[:amount.start()].strip(),
                    "dates": {"as_of": (date(line[:10]) if re.match(rf"^{DATE}\s+\(D", line)
                                         else opening_date if label.startswith("SALDO ANTERIOR") else end)},
                    "amounts": {"projected" if projected else "balance": _amount(amount)},
                    "attributes": {}, "source_refs": [ref]})
        flush()
    if not records:
        fail("O extrato XP está sem movimentações legíveis.")
    # Printed order is reverse chronological, including the physical order of
    # movements on the same day. Sorting only by date would corrupt balances.
    records.reverse()
    events, validations, running = [], [], opening
    for index, row in enumerate(records):
        if index and row["dates"]["transaction"] < records[index-1]["dates"]["transaction"]:
            fail("As movimentações XP não estão na ordem impressa esperada.")
        running += Decimal(row["amount"])
        validations.append(_check("xp_printed_running_balance", running, Decimal(row["balance"]), refs=row["refs"]))
        event = _event("investment_movement", dates=row["dates"], amounts={"movement": row["amount"], "balance": row["balance"]},
                       primary="movement", cash_effect=None, reference=None, description=row["description"], refs=row["refs"])
        events.append(event)
    validations.append(_check("xp_printed_closing_balance", running, closing))
    notice = ("Movimentações da conta investimento XP: datas de movimentação e liquidação, valores, saldos e projeções "
              "preservados no Excel/CSV. Posições e lançamentos futuros não são tratados como movimentos de conta corrente. "
              "OFX indisponível para este relatório de investimentos.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "XP Investimentos",
            "representation": "text", "evidence": [EVIDENCE], "events": events, "observations": observations,
            "validations": validations, "metadata": {"period_start": f"{period[2]}-{period[1]}-01", "period_end": end,
                "opening_as_of": opening_date,
                "opening_balance": str(opening), "closing_balance": str(closing)}, "notice": notice,
            "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                       "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}
