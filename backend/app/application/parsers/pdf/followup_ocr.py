"""Geometry and complete printed ledger controls for three explicit OCR variants."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions

SIGNED = rf"(?:-?{NUMBER}|{NUMBER}-)"


def _value(text):
    if not re.fullmatch(SIGNED, text.strip()):
        fail("Um valor do OCR está incompleto ou ilegível. Envie uma cópia mais nítida do extrato.")
    value = text.strip()
    return money(value.strip("-")) * (-1 if "-" in value else 1)


def _labels(page):
    return {upper(line.text): line for line in page.lines if line.bbox}


def _near(page, anchor):
    return [line for line in page.lines if line.bbox and abs(line.bbox["top"] - anchor.bbox["top"]) < .0035]


def _one(lines, left, right):
    candidates = [line for line in lines if left <= line.bbox["left"] < right]
    if len(candidates) != 1:
        fail("Não foi possível separar todas as colunas de valor e saldo do OCR.")
    return candidates[0]


def _source(row, page, cells):
    return replace(row, source_regions=evidence_regions(page.page_number, cells, words=page.words),
                   source_line_refs=tuple((page.page_number, cell.line_index) for cell in cells))


def _headers(page, keys):
    labels = _labels(page)
    found = {}
    for key in keys:
        matches = [line for text, line in labels.items() if text == key or text.startswith(key + " (")]
        if len(matches) != 1:
            return None
        found[key] = matches[0].bbox["left"]
    return found


def parse_stone_credit_debit(extraction):
    columns = _headers(extraction.pages[0], ("DATA", "TIPO", "LANCAMENTO", "VALOR", "SALDO", "CONTRAPARTE"))
    if not columns:
        fail("Não foi possível localizar o cabeçalho da variante Stone com crédito e débito.")
    rows, candidates = [], 0
    for page in extraction.pages:
        anchors = [line for line in page.lines if line.bbox and line.bbox["left"] < columns["TIPO"] - .02
                   and re.match(rf"^{DATE}(?:\s|$)", line.text)]
        amounts = [line for line in page.lines if line.bbox and columns["VALOR"] - .02 < line.bbox["left"] < columns["SALDO"] - .03
                   and re.search(r"\d[,\.]\d", line.text)]
        if not anchors and "CODIGO DE AUTENTICACAO" not in upper(" ".join(line.text for line in page.lines)):
            fail("Uma página Stone está sem lançamentos legíveis ou contém outro documento.")
        if len(amounts) != len(anchors):
            fail("O OCR Stone contém uma linha financeira sem data ou valor associado.")
        candidates += len(anchors)
        for index, anchor in enumerate(anchors):
            near = _near(page, anchor)
            value = _one(near, columns["VALOR"] - .02, columns["SALDO"] - .03)
            balance = _one(near, columns["SALDO"] - .03, columns["CONTRAPARTE"] - .01)
            direction = " ".join(upper(line.text) for line in near if line.bbox["left"] < columns["LANCAMENTO"] - .02)
            debit, credit = "DEBITO" in direction, "CREDITO" in direction
            if debit == credit:
                fail("O tipo de movimento Stone não está legível na coluna de crédito e débito.")
            amount = abs(_value(value.text)) * (-1 if debit else 1)
            if credit and _value(value.text) < 0:
                fail()
            y = anchor.bbox["top"]
            lower = (anchors[index - 1].bbox["top"] + y) / 2 if index else y - .025
            higher = (anchors[index + 1].bbox["top"] + y) / 2 if index + 1 < len(anchors) else y + .025
            cells = [line for line in page.lines if line.bbox and lower <= line.bbox["top"] < higher]
            description = " ".join(line.text for line in cells
                                   if columns["LANCAMENTO"] - .015 <= line.bbox["left"] < columns["VALOR"] - .015)
            rows.append(_source(_row(page.page_number, anchor.line_index, day(anchor.text[:10]), description,
                                     amount, _value(balance.text)), page, cells))
    if not rows or any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    rows.reverse()
    # This version prints the same net balance beside the credit and its fee.
    # It is a control for the pair, not the post-credit balance alone.
    paired = set()
    for index, (credit, fee) in enumerate(zip(rows, rows[1:])):
        if (credit.transaction.amount > 0 and fee.transaction.amount < 0
                and credit.transaction.date == fee.transaction.date and "TARIFA" in upper(fee.transaction.description)
                and credit.running_balance == fee.running_balance):
            paired.add(index)
    opening = Decimal(str(rows[0].running_balance)) - Decimal(str(rows[0].transaction.amount))
    if 0 in paired:
        opening -= Decimal(str(rows[1].transaction.amount))
    running, checks = opening, 0
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if index in paired:
            rows[index] = replace(row, running_balance=None)
        else:
            if running != Decimal(str(row.running_balance)):
                financial_mismatch("Os valores Stone não fecham com os saldos impressos, inclusive nas quebras de página.")
            checks += 1
    return VerifiedStatement(rows, "stone_credit_debit_ocr_v1", opening, running,
                             printed_balance_checks=checks, candidate_rows=candidates)


def parse_bradesco_columns(extraction):
    from app.application.parsers.pdf.final_ocr_statements import column_slope

    def value(text):
        if re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}", text.strip()):
            return Decimal(text.strip().replace(",", ""))
        return _value(text)

    columns = _headers(extraction.pages[0], ("DATA", "LANCAMENTO", "DCTO.", "CREDITO", "DEBITO", "SALDO"))
    if not columns:
        fail("O trecho Bradesco começa sem cabeçalho e data de movimento. Inclua a primeira página do extrato.")
    slopes = {}
    def position_y(page, line):
        if page.page_number not in slopes:
            slopes[page.page_number] = column_slope(page.lines, columns["CREDITO"]-.02, columns["SALDO"]-.03)
        return line.bbox["top"]-slopes[page.page_number]*line.bbox["left"]
    def near(page, anchor):
        return [line for line in page.lines if line.bbox and abs(position_y(page, line)-position_y(page, anchor)) < .006]
    seed = None
    current = None
    rows, checks, candidates = [], 0, 0
    printed_totals = []
    pages = []
    for page in extraction.pages:
        latest = min((line.bbox["top"] for line in page.lines if line.bbox and upper(line.text) == "ULTIMOS LANCAMENTOS"), default=1)
        page_totals = [line for line in page.lines if line.bbox and upper(line.text) == "TOTAL" and line.bbox["top"] < latest]
        if len(page_totals) > 1:
            fail()
        cutoff = latest
        if page_totals:
            total_cells = near(page, page_totals[0])
            printed_totals.append((
                value(_one(total_cells, columns["CREDITO"]-.02, columns["DEBITO"]-.03).text),
                value(_one(total_cells, columns["DEBITO"]-.03, columns["SALDO"]-.03).text),
                value(_one(total_cells, columns["SALDO"]-.03, 1).text),
            ))
            cutoff = page_totals[0].bbox["top"]-.0035
        if any(line.bbox and line.bbox["left"] < columns["LANCAMENTO"]-.03
               and re.match(r"\d{1,2}/\d{2}/\d{3,4}", line.text) and not re.fullmatch(DATE, line.text)
               for line in page.lines if line.bbox and line.bbox["top"] < cutoff):
            fail("Uma data Bradesco foi cortada ou está ilegível. Envie o extrato completo sem recortar a margem.")
        pages.append(replace(page, lines=[line for line in page.lines if not line.bbox or line.bbox["top"] < cutoff]))
    for page in pages:
        labels = _labels(page)
        opening = labels.get("SALDO ANTERIOR")
        if opening:
            if seed is not None or rows:
                fail()
            seed = running = value(_one(near(page, opening), columns["SALDO"] - .03, 1).text)
        anchors = [line for line in page.lines if line.bbox
                   and columns["DCTO."] - .025 < line.bbox["left"] < columns["CREDITO"] - .03
                   and re.fullmatch(r"\d{1,10}", line.text)]
        header = labels.get("CREDITO (R$)")
        minimum_y = header.bbox["top"] + .01 if header else 0
        financial_cells = [line for line in page.lines if line.bbox and line.bbox["top"] > minimum_y
                           and columns["CREDITO"]-.02 <= line.bbox["left"] < columns["SALDO"]-.03
                           and re.search(r"\d[,\.]\d", line.text)]
        if len(financial_cells) != len(anchors):
            fail("O trecho Bradesco contém um valor sem documento ou data associado. Envie o trecho completo.")
        candidates += len(anchors)
        for index, anchor in enumerate(anchors):
            aligned = near(page, anchor)
            dates = [line for line in aligned if line.bbox["left"] < columns["LANCAMENTO"] - .03
                     and re.fullmatch(DATE, line.text)]
            if dates:
                if len(dates) != 1 or current and day(dates[0].text) < current:
                    fail()
                current = day(dates[0].text)
            if current is None or seed is None:
                fail("Falta a data ou o saldo anterior no começo do trecho Bradesco. Inclua a primeira página.")
            amount_cell = _one(aligned, columns["CREDITO"] - .02, columns["SALDO"] - .03)
            balance = _one(aligned, columns["SALDO"] - .03, 1)
            amount = abs(value(amount_cell.text)) * (-1 if amount_cell.bbox["left"] >= columns["DEBITO"] - .03 else 1)
            if amount > 0 and value(amount_cell.text) < 0:
                fail()
            running += amount
            if running != value(balance.text):
                financial_mismatch("Os lançamentos Bradesco não fecham com os saldos impressos.")
            checks += 1
            y = position_y(page, anchor)
            lower = (position_y(page, anchors[index-1]) + y) / 2 if index else y - .01
            higher = (position_y(page, anchors[index+1]) + y) / 2 if index+1 < len(anchors) else y + .015
            cells = [line for line in page.lines if line.bbox and lower <= position_y(page, line) < higher]
            description = " ".join(line.text for line in cells if columns["LANCAMENTO"]-.02 <= line.bbox["left"] < columns["DCTO."]-.02)
            rows.append(_source(_row(page.page_number, anchor.line_index, current, description, amount,
                                     running, reference=anchor.text), page, cells))
    if not rows or checks != candidates:
        fail()
    if printed_totals:
        credits = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
        debits = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
        if printed_totals != [(credits, debits, running)]:
            financial_mismatch("Os totais do período Bradesco não fecham com os lançamentos extraídos.")
    return VerifiedStatement(rows, "bradesco_ocr_columns_validated_v1", seed, running,
                             partial=validate_statement_completeness(["\n".join(line.text for line in page.lines)
                                                                      for page in extraction.pages]),
                             printed_balance_checks=checks, candidate_rows=candidates)


def parse_banrisul_columns(extraction):
    first = extraction.pages[0]
    columns = _headers(first, ("OPERACAO", "DOCUMENTO", "VALOR", "SALDO", "SIST."))
    if not columns:
        fail("Não foi possível localizar as colunas do extrato Banrisul na imagem.")
    labels = _labels(first)
    opening = next((line for text, line in labels.items() if text.startswith("SALDO ANT EM ")), None)
    if opening is None:
        fail()
    period = re.search(rf"PERIODO:\s*({DATE})\s+\S+\s+({DATE})", " ".join(line.text for line in first.lines), re.I)
    if period is None:
        fail()
    start, end = day(period[1]), day(period[2])
    seed = running = _value(_one(_near(first, opening), columns["SALDO"] - .04, columns["SIST."]).text)
    rows, current, checks, candidates = [], None, 0, 0
    for page in extraction.pages:
        anchors = [line for line in page.lines if line.bbox and columns["DOCUMENTO"]-.035 < line.bbox["left"] < columns["VALOR"]-.05
                   and re.fullmatch(r"\d{6}", line.text)]
        header = _labels(page).get("VALOR")
        minimum_y = header.bbox["top"]+.01 if header else 0
        financial_cells = [line for line in page.lines if line.bbox and line.bbox["top"] > minimum_y
                           and columns["DOCUMENTO"]+.04 <= line.bbox["left"] < columns["SALDO"]-.03
                           and re.search(r"\d[,\.]\d", line.text)]
        if len(financial_cells) != len(anchors):
            fail("O OCR Banrisul contém um valor sem documento associado.")
        controls = [line for line in page.lines if line.bbox and upper(line.text) == "SALDO NA DATA"]
        for anchor in sorted([*anchors, *controls], key=lambda line:line.bbox["top"]):
            near = _near(page, anchor)
            if anchor in controls:
                control = _one(near, columns["SALDO"]-.04, columns["SIST."])
                if _value(control.text) != running or not rows:
                    fail("O OCR Banrisul não fecha com um saldo diário impresso.")
                rows[-1] = replace(rows[-1], running_balance=float(running))
                checks += 1
                continue
            candidates += 1
            description_cells = [line for line in near if line.bbox["left"] < columns["DOCUMENTO"]-.04]
            description = " ".join(line.text for line in description_cells)
            match = re.fullmatch(rf"(?:({DATE})\s+)?\d{{4}}\s+(.+)", description)
            if not match:
                fail("Uma linha Banrisul está sem operação ou descrição legível.")
            if match[1]:
                date = day(match[1])
                if not start <= date <= end or current and date < current:
                    fail()
                current = date
            if current is None:
                fail()
            value = _one(near, columns["DOCUMENTO"]+.04, columns["SALDO"]-.03)
            amount = _value(value.text)
            running += amount
            rows.append(_source(_row(page.page_number, anchor.line_index, current, match[2], amount,
                                     reference=anchor.text), page, near))
    if not rows or not checks or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, "banrisul_portal_ocr_v1", seed, running,
                             printed_balance_checks=checks, candidate_rows=candidates)
