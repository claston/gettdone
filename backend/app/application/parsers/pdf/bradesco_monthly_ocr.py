"""Bradesco monthly OCR: day/description/document/value and daily controls."""

import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions
from app.application.structured_statement_detection import BRADESCO_MONTHLY_OCR

MONTHS = dict(zip(("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
                   "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"), range(1, 13)))
VALUE = re.compile(rf"-?{NUMBER}-?")


def parse_bradesco_monthly(extraction):
    rows, opening, running, current_date = [], None, None, None
    checks, last_control = 0, -1
    for number, page in enumerate(extraction.pages, 1):
        text = upper(" ".join(line.text for line in page.lines))
        counters = re.findall(r"\bPAGINA:\s*(\d+)\s+DE\s+(\d+)\b", text)
        if counters != [(str(number), str(len(extraction.pages)))]:
            fail("Não foi possível conferir as páginas do extrato mensal Bradesco. Envie o documento completo e legível.")
        lines = sorted((line for line in page.lines if line.bbox), key=lambda line: (line.bbox["top"], line.bbox["left"]))
        headers = {label: next((line for line in lines if upper(line.text) == label), None)
                   for label in ("DIA", "HISTORICO", "N.DOCTO", "VALOR")}
        if not all(headers.values()):
            fail("Há uma página ilegível no extrato mensal Bradesco. Envie uma cópia nítida de todas as páginas.")
        date_x, description_x, document_x, value_x = [headers[label].bbox["left"] for label in headers]
        months = []
        for line in lines:
            match = re.fullmatch(r"([A-Z]+)/([0-9]{4})", upper(line.text))
            if match and match[1] in MONTHS:
                months.append((line.bbox["top"], MONTHS[match[1]], int(match[2])))
        if not months:
            fail()
        footer_y = min(line.bbox["top"] for line in lines if re.search(r"PAGINA:", upper(line.text)))
        values = [line for line in lines if headers["VALOR"].bbox["top"] < line.bbox["top"] < footer_y
                  and line.bbox["left"] >= value_x - .04 and re.search(r"\d[,.]\d", line.text)]
        if any(not VALUE.fullmatch(line.text) or line.confidence is not None and line.confidence < 85 for line in values):
            fail("Um valor do extrato mensal Bradesco está ilegível. Envie uma cópia mais nítida.")
        if not values:
            fail()
        for value_index, value in enumerate(values):
            y = value.bbox["top"]
            nearby = [line for line in lines if abs(line.bbox["top"] - y) < .004]
            label = upper(" ".join(line.text for line in nearby if description_x - .02 <= line.bbox["left"] < value_x - .04))
            token = value.text
            if token.startswith("-") and token.endswith("-"):
                fail()
            amount = money(token.strip("-")) * (-1 if "-" in token else 1)
            if "SALDO ANTERIOR" in label:
                if opening is not None or rows:
                    fail()
                opening = running = amount
                continue
            if label.startswith("SALDO EM"):
                date_cells = [line for line in nearby if re.fullmatch(r"\d{2}/\d{2}", line.text)]
                if (len(date_cells) != 1 or current_date is None
                        or date_cells[0].text != current_date[8:10] + "/" + current_date[5:7]
                        or running != amount or not rows):
                    fail("Os lançamentos do extrato mensal Bradesco não fecham com os saldos impressos.")
                rows[-1] = replace(rows[-1], running_balance=float(amount))
                last_control = len(rows) - 1
                checks += 1
                continue
            day_cells = [line for line in nearby if abs(line.bbox["left"] - date_x) < .025
                         and re.fullmatch(r"\d{1,2}", line.text)]
            applicable = [month for month in months if month[0] < y]
            if day_cells:
                if len(day_cells) != 1 or not applicable:
                    fail()
                _, month, year = applicable[-1]
                date = day(f"{int(day_cells[0].text):02d}/{month:02d}/{year}")
                if current_date and date < current_date:
                    fail()
                current_date = date
            if opening is None or current_date is None:
                fail()
            references = [line.text for line in nearby if abs(line.bbox["left"] - document_x) < .025
                          and re.fullmatch(r"\d{1,10}", line.text)]
            description_cells = [line for line in nearby if description_x - .02 <= line.bbox["left"] < document_x - .025]
            description = " ".join(line.text for line in description_cells)
            if not references:
                combined = re.fullmatch(r"(.+?)\s+(\d{1,10})", description)
                if combined:
                    description, reference = combined.groups()
                    references = [reference]
            if len(references) != 1 or not description:
                fail("Um valor do extrato mensal Bradesco não tem documento e data associados.")
            next_y = values[value_index + 1].bbox["top"] if value_index + 1 < len(values) else y + .004
            continuation = [line for line in lines if y + .004 <= line.bbox["top"] < next_y - .004
                            and description_x - .02 <= line.bbox["left"] < value_x - .04
                            and not re.fullmatch(r"\d{2}/\d{2}|[A-Za-z]+/\d{4}", line.text)]
            description = " ".join([description, *(line.text for line in continuation)])
            cells = [*nearby, *continuation]
            row = _row(number, value.line_index, current_date, description.strip(), amount, reference=references[0])
            row = replace(row, source_regions=evidence_regions(number, cells, words=page.words),
                          source_line_refs=tuple((number, cell.line_index) for cell in cells))
            rows.append(row)
            running += amount
    if not rows or not checks or last_control != len(rows) - 1:
        fail()
    return VerifiedStatement(rows, BRADESCO_MONTHLY_OCR, opening, running,
                             printed_balance_checks=checks, candidate_rows=len(rows))
