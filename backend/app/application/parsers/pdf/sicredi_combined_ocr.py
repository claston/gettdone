"""Sicredi statement cells: preserve every dated movement and its printed sign."""
import re
from dataclasses import replace
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "sicredi_signed_balance_columns_ocr_v1"
VALUE = re.compile(rf"[+-]?{NUMBER}")


def is_sicredi_combined(text):
    text = upper(text)
    return "SICREDI" in text and "DADOS REFERENTES AO PERIODO" in text


def parse_sicredi_combined(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    if not is_sicredi_combined(text):
        return None
    period = re.search(rf"DADOS REFERENTES AO PERIODO\s+({DATE})\s+A\s+({DATE})", text)
    if not period:
        fail("Falta um período legível no extrato Sicredi.")
    start, end = map(day, period.groups())
    rows, opening, running, checks, columns = [], None, None, 0, None
    for original in extraction.pages:
        lines = []
        annotated = set()
        words = {word.id: word for word in original.words}
        for line in original.lines:
            mark = re.fullmatch(rf"[A-Za-z¥*✓✔]\s*([+-]{NUMBER})", line.text.strip())
            if mark:
                # A margin X joined to an otherwise explicit signed amount is
                # an annotation, not an inferred digit or sign.
                line = replace(line, text=mark[1])
                annotated.add(line.id)
            # Textract may combine value and balance in one LINE. Split only
            # using its original WORD coordinates, never guessed column widths.
            if re.fullmatch(rf"{VALUE.pattern}\s+{VALUE.pattern}", line.text.strip()):
                cells = [words[key] for key in line.word_ids if key in words]
                if len(cells) != 2 or any(not VALUE.fullmatch(cell.text) for cell in cells):
                    fail("As colunas de valor e saldo Sicredi não estão separadas no OCR.")
                lines.extend(replace(line, id=cell.id, text=cell.text, bbox=cell.bbox, word_ids=(cell.id,)) for cell in cells)
            else:
                if re.match(rf"^{DATE}(?:\s|$)", line.text):
                    date_words = [words[key] for key in line.word_ids if key in words and words[key].text == line.text[:10]]
                    if len(date_words) == 1:
                        line = replace(line, bbox=date_words[0].bbox)
                lines.append(line)
        page = replace(original, lines=lines)
        headings = [line.bbox["top"] for line in lines if line.bbox and upper(line.text) == "DATA"]
        heading_lines = [line for line in lines if line.bbox and headings and abs(line.bbox["top"]-headings[0]) < .03
                         and upper(line.text) in {"DATA", "DESCRICAO", "DOCUMENTO", "VALOR (R$)", "SALDO (R$)", "VALOR", "SALDO"}]
        value_headers = [line for line in heading_lines if upper(line.text).startswith("VALOR")]
        if len(value_headers) == 1:
            heading_lines = [line for line in heading_lines if not upper(line.text).startswith("SALDO")
                             or line.bbox["left"] > value_headers[0].bbox["left"]]
        found = _headers(replace(page, lines=heading_lines), ("DATA", "DESCRICAO", "DOCUMENTO", "VALOR", "SALDO"))
        columns = found or columns
        if columns is None:
            fail("Falta o cabeçalho das colunas Sicredi.")
        header_y = max((line.bbox["top"] for line in lines if line.bbox and upper(line.text) == "DATA"), default=0)
        stop = min((line.bbox["top"] for line in lines if line.bbox and upper(line.text).startswith("LANCAMENTOS FUTUROS")), default=1)
        body = [line for line in lines if line.bbox and header_y < line.bbox["top"] < stop]
        def center(line):
            return line.bbox["left"] + line.bbox["width"]
        # Monetary columns are right aligned. Their right edges remain separate
        # even when short negative values start to the right of a centered header
        # or a photographed page has perspective distortion.
        edges = [center(line) for line in body if line.bbox["left"] >= columns["VALOR"]-.08 and VALUE.fullmatch(line.text)]
        if len(edges) < 2:
            fail("As colunas financeiras Sicredi não estão legíveis.")
        boundary = (min(edges)+max(edges))/2
        for _ in range(3):
            left, right = [x for x in edges if x < boundary], [x for x in edges if x >= boundary]
            if not left or not right or median(right)-median(left) < .03:
                fail("As colunas financeiras Sicredi estão sobrepostas.")
            boundary = (median(left)+median(right))/2
        # Scanned pages are often skewed; a merged date/history LINE also has
        # a different left coordinate than its monetary cells.
        from app.application.parsers.pdf.final_ocr_statements import column_slope

        centered = [replace(line, bbox={**line.bbox, "left": center(line)}) for line in body]
        slope = column_slope(centered, columns["VALOR"]-.08, boundary)
        if abs(slope) > .12:
            fail("A inclinação do extrato Sicredi impede separar as linhas.")
        def position(line):
            return line.bbox["top"]-slope*line.bbox["left"]
        used = set()
        if opening is None:
            seeds = [line for line in body if upper(line.text) in {"SALDO", "SALDO ANTERIOR"}
                     and line.bbox["left"] < columns["VALOR"]]
            balances = [line for line in body if seeds and center(line) >= boundary
                        and abs(position(line)-position(seeds[0])) < .012 and VALUE.fullmatch(line.text)]
            if len(seeds) != 1 or len(balances) != 1:
                fail("Falta um saldo inicial Sicredi legível.")
            opening = running = money(balances[0].text)
            used.add(balances[0].id)
        anchors = sorted((line for line in body if line.bbox["left"] < columns["DESCRICAO"]-.02
                          and re.match(rf"^{DATE}(?:\s|$)", line.text)), key=position)
        for index, anchor in enumerate(anchors):
            y = position(anchor)
            low = (position(anchors[index-1])+y)/2 if index else y-.015
            high = (position(anchors[index+1])+y)/2 if index+1 < len(anchors) else stop
            cells = [line for line in body if low <= position(line) < high]
            values = [line for line in cells if columns["VALOR"]-.08 <= center(line) < boundary and VALUE.fullmatch(line.text)]
            balances = [line for line in cells if center(line) >= boundary and VALUE.fullmatch(line.text)]
            if len(values) != 1 or len(balances) != 1:
                fail("Um movimento Sicredi está sem valor ou saldo legível associado.")
            value, balance = values[0], balances[0]
            if any(words[key].text_type == "HANDWRITING" for key in value.word_ids if key in words):
                fail("Um valor de movimento Sicredi está manuscrito ou foi alterado.")
            amount, printed = money(value.text), money(balance.text)
            date_text = anchor.text[:10]
            month_recovered = False
            if re.fullmatch(r"\d{2}/00/\d{4}", date_text) and start[:7] == end[:7] and date_text[-4:] == start[:4]:
                recovered = day(date_text[:3] + start[5:7] + date_text[5:])
                neighbors = []
                if rows:
                    neighbors.append(rows[-1].transaction.date)
                if index+1 < len(anchors):
                    neighbors.append(day(anchors[index+1].text[:10]))
                if not neighbors or any(d[:7] != start[:7] for d in neighbors):
                    fail("Uma data Sicredi não pode ser conferida com o período e as linhas vizinhas.")
                date = recovered
                month_recovered = True
            else:
                date = day(date_text)
            if rows and date < rows[-1].transaction.date:
                financial_mismatch("As datas impressas Sicredi estão fora de ordem; confira as linhas sinalizadas.")
            if not start <= date <= end or running + amount != printed:
                financial_mismatch()
            description = " ".join([anchor.text[10:].strip(), *(line.text for line in cells
                if min(columns["DESCRICAO"]-.02, columns["DATA"]+.04) <= line.bbox["left"] < columns["DOCUMENTO"]-.02
                and line.id != anchor.id)]).strip()
            if not description:
                fail("Um movimento Sicredi está sem descrição legível.")
            refs = [line.text for line in cells if columns["DOCUMENTO"]-.02 <= line.bbox["left"] < columns["VALOR"]-.08]
            rows.append(_source(_row(page.page_number, anchor.line_index, date, description, amount, printed,
                                     reference=" ".join(refs) or None), page, cells))
            if month_recovered or annotated.intersection((value.id, balance.id)):
                rows[-1] = replace(rows[-1], warnings=["manual_review_recommended"])
            running = printed
            checks += 1
            used.update((value.id, balance.id))
        monetary = {line.id for line in body if line.bbox["left"] >= columns["VALOR"]-.08 and VALUE.fullmatch(line.text)}
        if used != monetary:
            fail("Um valor Sicredi está sem movimento associado.")
        if stop != 1:
            break
    if not rows:
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, running, printed_balance_checks=checks, candidate_rows=len(rows))
