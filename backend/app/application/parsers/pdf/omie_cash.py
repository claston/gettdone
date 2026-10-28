"""Omie cash versus forecast columns, retaining unsettled financial events."""

import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event, date
from app.application.parsers.pdf.verified_statements import fail

_DECIMAL = r"[+-]?\d+\.\d{2}(?![\d.])"
_COLUMNS = re.compile(rf"(?<![\w.])({_DECIMAL})[ \t]+({_DECIMAL})[ \t]+({_DECIMAL})(?:[ \t]|$)")
_ROW = re.compile(r"^(Conciliado(?: \(bloqueado\))?|Atrasado|Em aberto|Previsto)[ \t]+(\d{2}/\d{2}/\d{4})[ \t]+(.+)$", re.I)
_CONTROL = re.compile(r"^(\d{2}/\d{2}/\d{4})[ \t]+SALDO(?: ANTERIOR)?[ \t]+(.+)$", re.I)


def is_omie_cash(text):
    return all(
        marker in upper(text) for marker in ("FINANCAS - MOVIMENTACAO DA CONTA CORRENTE", "SITUACAO", "SALDO PREVISTO (R$)", "OMIE.CASH")
    )


def parse_omie_cash(pages):
    events, observations, validations = [], [], []
    running = opening = None
    for page, content in enumerate(pages, 1):
        for number, raw in enumerate(content.splitlines(), 1):
            line = raw.strip()
            control, row = _CONTROL.match(line), _ROW.match(line)
            if not control and not row:
                continue
            body = control[2] if control else row[3]
            values = _COLUMNS.search(body)
            if not values:
                fail("As três colunas de valor, saldo e saldo previsto do relatório não estão legíveis.")
            amount, balance, forecast = map(Decimal, values.groups())
            refs = [{"page": page, "line": number}]
            if control:
                if amount != 0:
                    fail("Uma linha de saldo do relatório contém um movimento ambíguo.")
                if opening is None:
                    if "SALDO ANTERIOR" not in upper(line):
                        fail("O relatório não informa o saldo anterior da conta.")
                    opening = running = balance
                validations.append(_check("cash_daily_balance", running, balance, refs=refs))
                observations.append(
                    {
                        "observation_type": "cash_and_forecast_balance",
                        "dates": {"as_of": date(control[1])},
                        "amounts": {"balance": str(balance), "projected": str(forecast)},
                        "attributes": {},
                        "source_refs": refs,
                    }
                )
                continue
            if running is None:
                fail("O movimento aparece antes do saldo anterior do relatório.")
            settled = upper(row[1]).startswith("CONCILIADO")
            description = body[: values.start()].strip()
            if not description:
                fail("Um movimento do relatório está sem descrição.")
            cash = amount if settled else Decimal(0)
            running += cash
            validations.append(_check("cash_movement_balance", running, balance, refs=refs))
            events.append(
                _event(
                    "cash_movement" if settled else "receivable_title" if amount >= 0 else "payable_title",
                    dates={"transaction" if settled else "due": date(row[2])},
                    amounts={"movement": str(amount), "balance": str(balance), "projected": str(forecast)},
                    primary="movement",
                    cash_effect=str(amount) if settled else None,
                    reference=None,
                    description=description,
                    refs=refs,
                    status="settled" if settled else "overdue" if upper(row[1]) == "ATRASADO" else "pending",
                    attributes={"printed_status": row[1], "source_fields": {"row": line}},
                )
            )
    if not events or opening is None or not observations:
        fail()
    notice = (
        "Movimentos conciliados afetam o caixa. Títulos em aberto ou atrasados permanecem nos detalhes e no Excel; "
        "o OFX contém somente movimentos conciliados."
    )
    valid = all(v["status"] == "passed" for v in validations)
    return {
        "schema_version": "financial_events_v1",
        "document_type": "cash_flow_report",
        "evidence": ["omie_cash_actual_forecast_columns"],
        "origin": "Omie.CASH",
        "representation": "text",
        "events": events,
        "observations": observations,
        "metadata": {"opening_balance": str(opening), "closing_balance": str(running)},
        "validations": validations,
        "notice": notice,
        "export": {
            "available_formats": ["csv", "xlsx", "ofx"] if valid else ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": valid,
            "supports_row_edits": False,
            "reason": notice if valid else "Os movimentos diferem dos saldos impressos. Confira as validações no Excel.",
        },
    }
