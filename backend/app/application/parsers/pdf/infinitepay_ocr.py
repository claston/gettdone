"""Signed movement report, including rotated outlines and consecutive fragments."""

import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions
from app.application.sweep_statement_detection import INFINITEPAY

_MONTHS = dict(zip(("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"), range(1, 13)))
_DATE = re.compile(r"(\d{2}) (JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ), (20\d{2})")
_MONEY = re.compile(rf"([+-])\s*({NUMBER})")


def _date(text):
    match = _DATE.fullmatch(upper(text))
    if not match:
        fail()
    return day(f"{match[1]}/{_MONTHS[match[2]]:02}/{match[3]}")


def parse_infinitepay(extraction):
    rows, counters, current = [], [], None
    periods = set()
    for page in extraction.pages:
        for line in page.lines:
            matches = list(_DATE.finditer(upper(line.text)))
            if len(matches) == 2:
                periods.add(tuple(_date(match[0]) for match in matches))
    if len(periods) > 1:
        fail()
    bounds = next(iter(periods), None)
    if bounds and bounds[0] > bounds[1]:
        fail()
    for page in extraction.pages:
        original = {line.id: line for line in page.lines}
        labels = {upper(line.text): line for line in page.lines if line.bbox}
        if "HORA" not in labels or "VALOR (R$)" not in labels:
            fail()
        rotated = abs(labels["HORA"].bbox["top"] - labels["VALOR (R$)"].bbox["top"]) > 0.1
        lines = []
        for line in page.lines:
            if not line.bbox:
                if _MONEY.fullmatch(line.text.strip()):
                    fail()
                continue
            b = line.bbox
            box = {"left": 1 - b["top"] - b["height"], "top": b["left"], "width": b["height"], "height": b["width"]} if rotated else b
            lines.append(replace(line, bbox=box))
        labels = {upper(line.text): line for line in lines}
        time_x, value_x = labels["HORA"].bbox["left"], labels["VALOR (R$)"].bbox["left"]
        table_top = min(item.bbox["top"] for item in lines if upper(item.text) == "HORA")
        if not 0.03 < time_x < value_x - 0.2:
            fail()
        counter = re.findall(r"PAGINA (\d+) DE (\d+)", upper(" ".join(line.text for line in lines)))
        if len(counter) != 1:
            fail()
        counters.append(tuple(map(int, counter[0])))
        footer = min(
            (item.bbox["top"] for item in lines if "CENTRAL DE AJUDA" in upper(item.text) or "PAGINA " in upper(item.text)), default=1
        )
        times = sorted(
            [
                item
                for item in lines
                if re.fullmatch(r"\d{2}:\d{2}", item.text.strip()) and abs(item.bbox["left"] - time_x) < 0.04 and item.bbox["top"] < footer
            ],
            key=lambda item: item.bbox["top"],
        )
        dates = [item for item in lines if _DATE.fullmatch(upper(item.text)) and item.bbox["left"] < time_x - 0.02]
        if any(
            item.bbox["left"] < time_x - 0.02
            and table_top < item.bbox["top"] < footer
            and re.match(r"^\d{1,2}\s+[A-Z]{3}", upper(item.text))
            and _DATE.fullmatch(upper(item.text)) is None
            for item in lines
        ):
            fail("Uma data InfinitePay está ilegível. Não foi possível confirmar a data dos lançamentos desse grupo.")
        financial = [
            item
            for item in lines
            if item.bbox["left"] >= value_x - 0.05 and item.bbox["top"] < footer and re.fullmatch(rf"[+-]?\s*{NUMBER}", item.text.strip())
        ]
        if len(times) != len(financial):
            fail("Um valor InfinitePay está sem horário associado. Envie o trecho completo e legível.")
        used = set()
        for index, anchor in enumerate(times):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", anchor.text.strip()):
                fail("Um horário InfinitePay está inválido. Confira a qualidade do documento.")
            top = anchor.bbox["top"]
            eligible_dates = [item for item in dates if item.bbox["top"] <= top + 0.008]
            if eligible_dates:
                current = _date(max(eligible_dates, key=lambda item: item.bbox["top"]).text)
            if current is None:
                fail()
            if bounds and not bounds[0] <= current <= bounds[1]:
                fail("Uma data InfinitePay está fora do período impresso. Confira o documento original.")
            matches = [item for item in financial if abs(item.bbox["top"] - top) < 0.008]
            if len(matches) != 1 or matches[0].id in used:
                fail()
            value = matches[0]
            signed = _MONEY.fullmatch(value.text.strip())
            if not signed:
                fail("O valor InfinitePay está sem sinal explícito. Não foi possível confirmar a direção do lançamento.")
            used.add(value.id)
            next_top = times[index + 1].bbox["top"] - 0.008 if index + 1 < len(times) else min(top + 0.035, footer)
            descriptions = [
                item
                for item in lines
                if time_x + 0.045 < item.bbox["left"] < value_x - 0.05
                and top - 0.008 <= item.bbox["top"] < next_top
                and upper(item.text) not in {"TIPO DE TRANSACAO", "NOME", "DETALHE"}
            ]
            if not descriptions or not any(abs(item.bbox["top"] - top) < 0.008 for item in descriptions):
                fail()
            witnesses = [original[item.id] for item in [anchor, value, *descriptions, *eligible_dates[-1:]]]
            if any(word.text_type == "HANDWRITING" and word.id in value.word_ids for word in page.words):
                fail()
            amount = money(signed[2]) * (-1 if signed[1] == "-" else 1)
            row = _row(page.page_number, anchor.line_index, current, " ".join(item.text for item in descriptions), amount)
            rows.append(
                replace(
                    row,
                    source_regions=evidence_regions(page.page_number, witnesses, words=page.words),
                    source_line_refs=tuple((page.page_number, item.line_index) for item in witnesses),
                )
            )
        if len(used) != len(financial):
            fail()
    if not rows or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    first, total = counters[0]
    if counters != [(n, total) for n in range(first, first + len(counters))] or counters[-1][0] > total:
        fail("Falta uma página entre os trechos InfinitePay. Envie páginas consecutivas.")
    return VerifiedStatement(rows, INFINITEPAY, None, None, partial=first != 1 or counters[-1][0] != total)
