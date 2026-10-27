"""XP web brokerage ledger: Liq precedes Mov, projections are observations."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.verified_statements import DATE, day, fail
from app.application.parsers.pdf.xp_investment_cash import CURRENCY, _amount


def parse_xp_account_web(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    periods = set(re.findall(rf"DE:\s*({DATE})\s+ATE:\s*({DATE})", text))
    accounts = set(re.findall(r"\bCONTA:\s*([\d-]+)", text))
    if len(periods) != 1 or len(accounts) != 1:
        fail("A conta ou o período XP não pode ser confirmado.")
    first, last = next(iter(periods))
    start, end = day(first), day(last)
    if start > end:
        fail()
    records, observations = [], []
    for page in source.pages:
        headers = {}
        for label in ("LIQ", "MOV", "HISTORICO", "VALOR", "SALDO"):
            matches = sorted((line for line in page.lines if upper(line.text) == label), key=lambda line: line.bbox["top"])
            if not matches:
                fail("O extrato XP está sem suas cinco colunas legíveis.")
            headers[label] = matches[0]
        xs = [headers[label].bbox["left"] for label in headers]
        if xs != sorted(xs) or max(h.bbox["top"] for h in headers.values()) - min(h.bbox["top"] for h in headers.values()) > .015:
            fail()
        top = headers["LIQ"].bbox["top"]
        limits = [line.bbox["top"] for line in page.lines if upper(line.text) == "LANCAMENTOS FUTUROS"]
        if len(limits) != 1:
            fail("A separação dos lançamentos futuros XP não está legível.")
        future_top = limits[0]
        endings = [line.bbox["top"] for line in page.lines if upper(line.text).startswith("EXTRATO SIMPLES PARA CONFERENCIA")]
        if len(endings) != 1:
            fail()
        bottom = endings[0]
        used = set()
        for future, low, high in ((False, top, future_top), (True, future_top, bottom)):
            dates = sorted((line for line in page.lines if low < line.bbox["top"] < high
                and line.bbox["left"] < xs[1] - .03 and re.fullmatch(DATE, line.text)), key=lambda line: line.bbox["top"])
            for index, anchor in enumerate(dates):
                y = anchor.bbox["top"]
                stop = dates[index+1].bbox["top"] if index+1 < len(dates) else high
                cells = [line for line in page.lines if y-.006 <= line.bbox["top"] < stop-.006]
                movement = [line for line in cells if xs[1]-.02 <= line.bbox["left"] < xs[2]-.03 and re.fullmatch(DATE, line.text)]
                amounts = sorted((line for line in cells if line.bbox["left"] >= xs[3]-.035 and "R$" in line.text),
                    key=lambda line: line.bbox["left"])
                history = [line for line in cells if xs[2]-.015 <= line.bbox["left"] < xs[3]-.035]
                if len(movement) != 1 or len(amounts) != 2 or not history or any(not CURRENCY.fullmatch(line.text) for line in amounts):
                    fail("Uma linha XP está sem suas duas datas, descrição, valor ou saldo legíveis.")
                settlement, transaction = day(anchor.text), day(movement[0].text)
                if not future and not (start <= settlement <= end and start <= transaction <= end):
                    fail("Uma data XP está fora do período selecionado.")
                used.update(line.id for line in amounts)
                record = {"dates": {"transaction": transaction, "settlement": settlement},
                    "description": " ".join(line.text for line in history),
                    "amount": _amount(CURRENCY.fullmatch(amounts[0].text)), "balance": _amount(CURRENCY.fullmatch(amounts[1].text)),
                    "refs": [{"page": page.page_number, "line": line.line_index} for line in cells]}
                if future:
                    observations.append({"observation_id": f"future_p{page.page_number}_l{anchor.line_index}",
                        "observation_type": "account_projection", "description": record["description"], "dates": record["dates"],
                        "amounts": {"projected": record["amount"], "balance": record["balance"]},
                        "attributes": {}, "source_refs": record["refs"]})
                else:
                    records.append(record)
        ledger_cash = {line.id for line in page.lines if top < line.bbox["top"] < bottom and "R$" in line.text
                       and line.bbox["left"] >= xs[3]-.035}
        if used != ledger_cash:
            fail("Há um valor XP sem lançamento associado.")
        for line in page.lines:
            if line.bbox["top"] >= top or "R$" not in line.text:
                continue
            match = CURRENCY.search(line.text)
            if not match:
                fail()
            label = line.text[:match.start()].strip()
            refs = [line]
            if not label:
                labels = [other for other in page.lines if other.bbox["left"] < xs[3]-.035
                          and abs(other.bbox["top"] - line.bbox["top"]) < .006]
                if len(labels) != 1:
                    fail()
                label = labels[0].text
                refs += labels
            projected = "PROJE" in upper(label) or "FUTUR" in upper(label)
            observations.append({"observation_id": f"position_p{page.page_number}_l{line.line_index}",
                "observation_type": "account_projection" if projected else "cash_position", "description": label,
                "dates": {}, "amounts": {"projected" if projected else "balance": _amount(match)},
                "attributes": {"scope": "consultation_snapshot"},
                "source_refs": [{"page": page.page_number, "line": ref.line_index} for ref in refs]})
    if not records:
        fail("O extrato XP está sem movimentações legíveis.")
    records.reverse()
    if any(a["dates"]["settlement"] > b["dates"]["settlement"] for a, b in zip(records, records[1:])):
        fail("A ordem de liquidação XP não pode ser confirmada.")
    validations = [_check("xp_printed_running_balance", Decimal(a["balance"]) + Decimal(b["amount"]),
                         Decimal(b["balance"]), refs=b["refs"]) for a, b in zip(records, records[1:])]
    events = [_event("investment_movement", dates=row["dates"], amounts={"movement": row["amount"], "balance": row["balance"]},
        primary="movement", cash_effect=None, reference=None, description=row["description"], refs=row["refs"]) for row in records]
    notice = ("Movimentações da conta investimento XP: datas de movimentação e liquidação, valores e saldos preservados. "
        "Posições da consulta e lançamentos futuros ficam separados. OFX indisponível para este relatório de investimentos.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "XP Investimentos",
        "representation": "native_text", "evidence": ["xp_account_web_ledger"], "events": events,
        "observations": observations, "validations": validations,
        "metadata": {"period_start": start, "period_end": end, "latest_ledger_balance": records[-1]["balance"],
                     "latest_ledger_balance_as_of": records[-1]["dates"]["settlement"]}, "notice": notice,
        "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
            "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}
