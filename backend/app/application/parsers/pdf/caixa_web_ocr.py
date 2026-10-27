"""Caixa's separate amount/Saldo rows, with complete monetary-cell coverage."""
import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions

_VALUE = re.compile(rf"({NUMBER}|\d[\d.]*\s+\d{{2}})\s*([CD]?)", re.I)
LAYOUT = "caixa_extrato_por_periodo_web_v1"


def _value(line):
    match = _VALUE.fullmatch(line.text.strip())
    if not match:
        fail()
    amount = money(re.sub(r"\s+", ",", match[1]))
    if not match[2] and amount != 0:
        fail("Um valor Caixa está sem indicador C/D legível.")
    return amount * (-1 if match[2].upper() == "D" else 1)


def _column_lines(page, value_x):
    words = {word.id: word for word in page.words}
    lines = []
    for line in page.lines:
        if not line.bbox:
            continue
        cells = [words[key] for key in line.word_ids if key in words]
        groups = ([word for word in cells if word.bbox["left"] < value_x - .08],
                  [word for word in cells if word.bbox["left"] >= value_x - .08])
        if cells and all(groups):
            for role, group in zip(("label", "cash"), groups, strict=True):
                group = sorted(group, key=lambda word: word.bbox["left"])
                left = min(word.bbox["left"] for word in group)
                right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                lines.append(replace(line, id=line.id + ":" + role, text=" ".join(word.text for word in group),
                    bbox={**line.bbox, "left": left, "width": right - left}, word_ids=tuple(word.id for word in group)))
        else:
            lines.append(line)
    return lines


def parse_caixa_web_ocr(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    if not all(marker in text for marker in ("EXTRATO POR PERIODO", "DATA MOV.", "HISTORICO", "SALDO ANTERIOR")):
        return None
    if not any(upper(line.text) == "VALOR" for line in source.pages[0].lines):
        return None
    value_header = next(line for line in source.pages[0].lines if upper(line.text) == "VALOR")
    if any(upper(line.text) == "SALDO" and abs(line.bbox["top"] - value_header.bbox["top"]) < .016
           for line in source.pages[0].lines):
        return None  # A separate balance column belongs to another Caixa reader.
    rows, declared, running, initial = [], None, None, None
    ambiguity, checks, all_dates = "", 0, []
    value_x = next(line.bbox["left"] for line in source.pages[0].lines if upper(line.text) == "VALOR")
    for page_index, page in enumerate(source.pages):
        lines = _column_lines(page, value_x)
        cash = [line for line in lines if line.bbox["left"] >= value_x - .08 and _VALUE.fullmatch(line.text.strip())]
        used = set()

        def near(anchor):
            values = [line for line in cash if abs(line.bbox["top"] - anchor.bbox["top"]) < .016]
            if len(values) != 1:
                fail("Não foi possível associar um valor ou saldo Caixa à sua linha.")
            used.add(values[0].id)
            return values[0]

        for label in [line for line in lines if upper(line.text) == "SALDO ANTERIOR"]:
            if declared is not None or rows:
                fail("O extrato Caixa contém mais de um saldo anterior.")
            declared = _value(near(label))
        dates = sorted([line for line in lines if line.bbox["left"] < .1 and re.match(rf"^{DATE}(?:\s|$)", line.text)],
                       key=lambda line: line.bbox["top"])
        balances = [(line, near(line)) for line in lines if upper(line.text) == "SALDO"]
        if declared is None:
            fail("O saldo anterior Caixa não foi identificado.")
        if initial is None:
            pre = [value for label, value in balances if not dates or label.bbox["top"] < dates[0].bbox["top"]]
            if len(pre) != 1:
                fail("Não foi possível conferir o saldo Caixa anterior ao primeiro movimento.")
            initial = running = _value(pre[0])
            if initial != declared:
                ambiguity = "conflicting_printed_balances"
        for index, anchor in enumerate(dates):
            date = day(anchor.text[:10])
            if all_dates and date < all_dates[-1]:
                fail("As páginas Caixa não estão na ordem das datas impressas.")
            all_dates.append(date)
            top = anchor.bbox["top"]
            stop = dates[index + 1].bbox["top"] if index + 1 < len(dates) else 1
            cells = [line for line in lines if top - .008 <= line.bbox["top"] < stop - .008]
            full = re.fullmatch(rf"{DATE}\s+(\d+)\s+(.+)", anchor.text)
            if full:
                reference, description = full[1], full[2]
            else:
                refs = [line.text for line in cells if .1 < line.bbox["left"] < .19 and re.fullmatch(r"\d+", line.text)]
                history = [line.text for line in cells if .18 <= line.bbox["left"] < .4]
                if len(refs) != 1 or not history:
                    fail("Um movimento Caixa está sem documento ou histórico legível.")
                reference, description = refs[0], " ".join(history)
            amount_cell = near(anchor)
            after = [value for label, value in balances if top + .008 < label.bbox["top"] < stop - .008]
            continuation = None
            if not after and index + 1 == len(dates) and page_index + 1 < len(source.pages):
                next_page = source.pages[page_index + 1]
                next_lines = _column_lines(next_page, value_x)
                first_date_y = min((line.bbox["top"] for line in next_lines
                                   if line.bbox["left"] < .1 and re.match(rf"^{DATE}(?:\s|$)", line.text)), default=1)
                labels = [line for line in next_lines if upper(line.text) == "SALDO" and line.bbox["top"] < first_date_y]
                if len(labels) == 1:
                    values = [line for line in next_lines if line.bbox["left"] >= value_x - .08
                              and _VALUE.fullmatch(line.text.strip()) and abs(line.bbox["top"] - labels[0].bbox["top"]) < .016]
                    if len(values) == 1:
                        after = values
                        continuation = next_page, [labels[0], values[0]]
            if len(after) != 1:
                fail("Um movimento Caixa não tem seu saldo impresso associado.")
            printed = _value(after[0])
            if re.fullmatch(r"SALDO (?:DO )?DIA", upper(description)):
                if _value(amount_cell) != running or printed != running:
                    financial_mismatch()
                checks += 2
                continue
            amount = _value(amount_cell)
            if running + amount != printed:
                financial_mismatch()
            row = _source(_row(page.page_number, anchor.line_index, date, description, amount, printed, reference), page, cells)
            if continuation:
                next_page, witnesses = continuation
                regions = evidence_regions(next_page.page_number, witnesses, words=next_page.words)
                row = replace(row, source_regions=(*row.source_regions, *regions),
                              source_line_refs=(*row.source_line_refs, *((next_page.page_number, line.line_index) for line in witnesses)))
            if re.search(r"\d\s+\d{2}\s*[CD]$", amount_cell.text, re.I):
                row = replace(row, warnings=[*row.warnings, "ocr_numeric_separator_normalized"])
            rows.append(row)
            running = printed
            checks += 1
        if used != {line.id for line in cash}:
            fail("Há um valor Caixa sem linha ou saldo associado; a conversão não pode omiti-lo.")
    if not rows:
        fail()
    return VerifiedStatement(rows, LAYOUT, declared, running, printed_balance_checks=checks,
                             candidate_rows=len(rows), balance_checkpoints={0: float(initial)}, opening_ambiguity_reason=ambiguity)
