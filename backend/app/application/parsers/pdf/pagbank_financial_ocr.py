"""PagBank financial report: available cash and receivables are separate ledgers."""

import re
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import (
    optional_document_period,
    period_contains,
)
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.followup_ocr import _headers
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money

_DATETIME = re.compile(rf"^({DATE})\s+(\d{{2}}:\d{{2}}:\d{{2}})$")
_MONEY = re.compile(rf"^[+-]?{NUMBER}$")
_ACCOUNTS = {"DISPONIVEL": "available", "A RECEBER": "receivables", "BLOQUEADO": "blocked"}


def _split_merged_lines(page, columns):
    words = {w.id: w for w in page.words if w.bbox}
    boundaries = [columns[k] - 0.015 for k in ("CODIGO DA TRANSACAO", "DESCRICAO", "CONTA", "VALOR(R$)")]
    result = []
    for line in page.lines:
        if not line.bbox or not line.word_ids or not all(w in words for w in line.word_ids):
            result.append(line)
            continue
        groups = defaultdict(list)
        for word_id in line.word_ids:
            word = words[word_id]
            role = sum(word.bbox["left"] >= boundary for boundary in boundaries)
            groups[role].append(word)
        if len(groups) == 1:
            result.append(line)
            continue
        for role, group in sorted(groups.items()):
            group.sort(key=lambda w: w.word_index)
            left = min(w.bbox["left"] for w in group)
            top = min(w.bbox["top"] for w in group)
            right = max(w.bbox["left"] + w.bbox["width"] for w in group)
            bottom = max(w.bbox["top"] + w.bbox["height"] for w in group)
            result.append(
                replace(
                    line,
                    id=f"{line.id}:column{role}",
                    text=" ".join(w.text for w in group),
                    bbox={"left": left, "top": top, "width": right - left, "height": bottom - top},
                    word_ids=tuple(w.id for w in group),
                )
            )
    return result


def parse_pagbank_financial(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    period = re.search(rf"PERIODO:\s*({DATE})\s+A\s+({DATE})", text)
    start, end = optional_document_period(period)
    events, observations, changes = [], [], defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    controls, event_dates = {}, set()
    for ordinal, page in enumerate(extraction.pages, 1):
        if ordinal != page.page_number:
            fail("As páginas do relatório PagBank não estão em sequência. Envie o relatório completo.")
        columns = _headers(page, ("DATA", "CODIGO DA TRANSACAO", "DESCRICAO", "CONTA", "VALOR(R$)"))
        if not columns:
            fail("Não foi possível localizar as colunas do relatório financeiro PagBank.")
        lines = [line for line in _split_merged_lines(page, columns) if line.bbox]
        for index, line in enumerate(lines):
            if upper(line.text) == "DATA:":
                date_cells = [
                    item
                    for item in lines
                    if re.fullmatch(DATE, item.text)
                    and abs(item.bbox["top"] - line.bbox["top"]) < 0.006
                    and line.bbox["left"] < item.bbox["left"] < columns["CODIGO DA TRANSACAO"]
                ]
                if len(date_cells) != 1:
                    fail()
                lines[index] = replace(line, text=f"Data: {date_cells[0].text}")
        anchors = [line for line in lines if _DATETIME.fullmatch(line.text) and line.bbox["left"] < columns["CODIGO DA TRANSACAO"] - 0.02]
        summaries = [line for line in lines if re.fullmatch(rf"DATA:\s*{DATE}", upper(line.text))]
        boundaries = sorted([*anchors, *summaries], key=lambda item: item.bbox["top"])
        used_values = set()
        for index, anchor in enumerate(boundaries):
            top = anchor.bbox["top"]
            bottom = boundaries[index + 1].bbox["top"] - 0.005 if index + 1 < len(boundaries) else 1
            cells = [item for item in lines if top - 0.005 <= item.bbox["top"] < bottom]
            refs = [{"page": page.page_number, "line": item.line_index} for item in cells]
            if anchor in summaries:
                date = day(re.search(DATE, anchor.text)[0])
                summary = upper(" ".join(item.text for item in sorted(cells, key=lambda item: item.line_index)))
                amounts = {}
                for label, key in _ACCOUNTS.items():
                    values = re.findall(re.escape(label) + rf":\s*([+-]?{NUMBER})", summary)
                    if len(values) != 1:
                        fail("Um saldo diário PagBank está incompleto ou ilegível. Envie o relatório completo.")
                    amounts[key] = format(money(values[0]), ".2f")
                if date in controls:
                    if controls[date] != amounts:
                        fail()
                    continue
                controls[date] = amounts
                observations.append(
                    {
                        "observation_type": "payment_subaccount_daily_balances",
                        "dates": {"as_of": date},
                        "amounts": amounts,
                        "attributes": {},
                        "source_refs": refs,
                    }
                )
                continue
            match = _DATETIME.fullmatch(anchor.text)
            date, time = day(match[1]), match[2]
            if not period_contains(date, start, end) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", time):
                fail()
            aligned = [item for item in cells if abs(item.bbox["top"] - top) < 0.006]
            accounts = [item for item in aligned if columns["CONTA"] - 0.015 <= item.bbox["left"] < columns["VALOR(R$)"] - 0.025]
            values = [item for item in aligned if item.bbox["left"] >= columns["VALOR(R$)"] - 0.025 and _MONEY.fullmatch(item.text.strip())]
            codes = [item for item in aligned if columns["CODIGO DA TRANSACAO"] - 0.015 <= item.bbox["left"] < columns["DESCRICAO"] - 0.025]
            codes = [item for item in codes if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{3,}", item.text)]
            if len(accounts) != 1 or len(values) != 1 or len(codes) != 1 or values[0].id in used_values:
                fail("Uma linha PagBank está sem conta, código ou valor associado. Envie uma cópia legível.")
            if upper(accounts[0].text) not in _ACCOUNTS:
                fail()
            key = _ACCOUNTS[upper(accounts[0].text)]
            description = " ".join(
                item.text
                for item in sorted(cells, key=lambda item: item.line_index)
                if columns["DESCRICAO"] - 0.015 <= item.bbox["left"] < columns["CONTA"] - 0.025
            )
            if not description or any(w.text_type == "HANDWRITING" and w.id in values[0].word_ids for w in page.words):
                fail()
            amount = money(values[0].text.strip())
            used_values.add(values[0].id)
            changes[date][key] += amount
            event_dates.add(date)
            event = _event(
                "payment_account_entry",
                dates={"transaction": date},
                amounts={"signed_value": format(amount, ".2f")},
                primary="signed_value",
                cash_effect=format(amount, ".2f") if key == "available" else None,
                reference=codes[0].text,
                description=description,
                refs=refs,
                attributes={"subaccount": key, "time": time, "printed_account": accounts[0].text},
            )
            events.append(event)
        orphan = [
            item
            for item in lines
            if item.bbox["left"] >= columns["VALOR(R$)"] - 0.025
            and _MONEY.fullmatch(item.text.strip())
            and item.id not in used_values
            and not any(
                s.bbox["top"] - 0.005
                <= item.bbox["top"]
                < min((b.bbox["top"] for b in boundaries if b.bbox["top"] > s.bbox["top"] + 0.006), default=1)
                for s in summaries
            )
        ]
        if orphan:
            fail("O relatório PagBank contém um valor sem linha associada.")
    if not events or set(controls) != event_dates:
        fail("Falta um saldo diário ou uma página do relatório financeiro PagBank.")
    events.sort(key=lambda e: (e["dates"]["transaction"], e["attributes"]["time"]))
    dates = sorted(controls)
    validations = []
    for previous, current in zip(dates, dates[1:]):
        for key in _ACCOUNTS.values():
            expected = Decimal(controls[previous][key]) + changes[current][key]
            validations.append(_check(f"pagbank_{key}_{current}", expected, Decimal(controls[current][key])))
    notice = (
        "Relatório financeiro PagBank: disponível, a receber e bloqueado são contas distintas. "
        "Movimentos, horários, códigos e saldos diários estão preservados no CSV e Excel. "
        "Não há saldo inicial impresso; a conferência compara os saldos diários consecutivos. "
        "Revise as validações antes de usar os valores. OFX indisponível para este relatório misto."
    )
    return {
        "schema_version": "financial_events_v1",
        "document_type": "payment_account_financial_report",
        "origin": "PagBank",
        "evidence": ["pagbank_financial_subaccounts_columns"],
        "representation": "ocr_text",
        "events": events,
        "observations": observations,
        "validations": validations,
        "metadata": {"period_start": start, "period_end": end, "opening_balance_printed": False},
        "notice": notice,
        "export": {
            "available_formats": ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": False,
            "supports_row_edits": False,
            "reason": notice,
        },
    }
