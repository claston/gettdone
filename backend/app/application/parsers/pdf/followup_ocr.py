"""Geometry and complete printed ledger controls for three explicit OCR variants."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
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
                fail("Os valores Stone não fecham com os saldos impressos, inclusive nas quebras de página.")
            checks += 1
    return VerifiedStatement(rows, "stone_credit_debit_ocr_v1", opening, running,
                             printed_balance_checks=checks, candidate_rows=candidates)


def parse_bradesco_columns(extraction):
    columns = _headers(extraction.pages[0], ("DATA", "LANCAMENTO", "DCTO.", "CREDITO", "DEBITO", "SALDO"))
    if not columns:
        fail("O trecho Bradesco começa sem cabeçalho e data de movimento. Inclua a primeira página do extrato.")
    counters = []
    for page in extraction.pages:
        matches = re.findall(r"FOLHA\s+(\d+)\s*/\s*(\d+)", " ".join(upper(line.text) for line in page.lines))
        if len(matches) != 1:
            fail("Não foi possível conferir a sequência de páginas Bradesco.")
        counters.append(tuple(map(int, matches[0])))
    first, total = counters[0]
    if counters != [(n, total) for n in range(first, first + len(counters))] or first != 1:
        fail("Inclua a primeira página e mantenha as páginas Bradesco em sequência.")
    seed = None
    current = None
    rows, checks, candidates = [], 0, 0
    for page in extraction.pages:
        labels = _labels(page)
        opening = labels.get("SALDO ANTERIOR")
        if opening:
            if seed is not None or rows:
                fail()
            seed = running = _value(_one(_near(page, opening), columns["SALDO"] - .03, 1).text)
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
            near = _near(page, anchor)
            dates = [line for line in near if line.bbox["left"] < columns["LANCAMENTO"] - .03
                     and re.fullmatch(DATE, line.text)]
            if dates:
                if len(dates) != 1 or current and day(dates[0].text) < current:
                    fail()
                current = day(dates[0].text)
            if current is None or seed is None:
                fail("Falta a data ou o saldo anterior no começo do trecho Bradesco. Inclua a primeira página.")
            value = _one(near, columns["CREDITO"] - .02, columns["SALDO"] - .03)
            balance = _one(near, columns["SALDO"] - .03, 1)
            amount = abs(_value(value.text)) * (-1 if value.bbox["left"] >= columns["DEBITO"] - .03 else 1)
            if amount > 0 and _value(value.text) < 0:
                fail()
            running += amount
            if running != _value(balance.text):
                fail("Os lançamentos Bradesco não fecham com os saldos impressos.")
            checks += 1
            y = anchor.bbox["top"]
            lower = (anchors[index-1].bbox["top"] + y) / 2 if index else y - .01
            higher = (anchors[index+1].bbox["top"] + y) / 2 if index+1 < len(anchors) else y + .01
            cells = [line for line in page.lines if line.bbox and lower <= line.bbox["top"] < higher]
            description = " ".join(line.text for line in cells if columns["LANCAMENTO"]-.02 <= line.bbox["left"] < columns["DCTO."]-.02)
            rows.append(_source(_row(page.page_number, anchor.line_index, current, description, amount,
                                     running, reference=anchor.text), page, cells))
    if not rows or checks != candidates:
        fail()
    return VerifiedStatement(rows, "bradesco_ocr_columns_validated_v1", seed, running,
                             partial=counters[-1][0] != total, printed_balance_checks=checks, candidate_rows=candidates)


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
