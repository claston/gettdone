"""Explicit OCR columns, row coverage and independent statement periods."""
import re
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.final_native_statements import MONTHS
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

SICREDI = "sicredi_signed_balance_columns_ocr_v1"
CAIXA = "caixa_internet_independent_periods_ocr_v1"
BB = "bb_cash_rende_facil_combined_v1"
VALUE = re.compile(rf"[+-]?(?:{NUMBER}|\d+\.\d{{2}})")
GLYPH = re.compile(r"Z\.\d{3}(?:\.\d{3})*,\d{2}")


def column_slope(lines, left, boundary):
    amounts = [line for line in lines if line.bbox and left <= line.bbox["left"] < boundary and VALUE.fullmatch(line.text)]
    balances = [line for line in lines if line.bbox and line.bbox["left"] >= boundary and VALUE.fullmatch(line.text)]
    slopes = []
    for amount in amounts:
        matches = sorted(balances, key=lambda line: abs(line.bbox["top"]-amount.bbox["top"]))
        if matches and abs(matches[0].bbox["top"]-amount.bbox["top"]) < .01:
            other = matches[0]
            slopes.append((other.bbox["top"]-amount.bbox["top"])/(other.bbox["left"]-amount.bbox["left"]))
    slope = median(slopes) if slopes else 0
    if abs(slope) > .12:
        fail("A inclinação da página impede a associação segura das colunas.")
    return slope


def identify_final_ocr(text):
    text = upper(text)
    if "CONTA CORRENTE + RENDE FACIL" in text and "BANCO DO BRASIL" in text and "VALOR R$" in text:
        return BB
    if "SICREDI" in text and "PERIODO DE" in text and all(key in text for key in ("VALOR (R$)", "SALDO (R$)", "COOPERATIVA:")):
        return SICREDI
    if "GERENCIADOR" in text and "CAIXA" in text and all(key in text for key in ("VIA INTERNET", "LANCAMENTOS DE", "MOVIMENTO")):
        return CAIXA
    return None


def parse_final_ocr(extraction):
    kind = identify_final_ocr("\n".join(line.text for page in extraction.pages for line in page.lines))
    if kind is None:
        return None
    try:
        return {BB: _bb, SICREDI: _sicredi, CAIXA: _caixa}[kind](extraction)
    except PdfFinancialValidationError as exc:
        setattr(exc, "_parse_observability", {**getattr(exc, "_parse_observability", {}),
                "layout_inference_name": kind, "layout_inference_confidence": .99,
                "selected_parser": "layout_specific_controlled_ocr"})
        raise


def _number(cell):
    value = cell.text.strip()
    if not VALUE.fullmatch(value):
        fail("Um valor ou saldo OCR está incompleto ou ilegível.")
    return money(value) if "," in value else Decimal(value)


def _one(cells, left, right, *, allow_glyph=False):
    values = [cell for cell in cells if left <= cell.bbox["left"] < right
              and (VALUE.fullmatch(cell.text.strip()) or allow_glyph and GLYPH.fullmatch(cell.text.strip()))]
    if len(values) != 1:
        fail("Não foi possível associar um único valor à coluna do OCR.")
    return values[0]


def _bb(extraction):
    rows, candidates = [], 0
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    period = re.search(r"MOVIMENTACAO EM\s+("+"|".join(MONTHS)+r") DE (\d{4})", text)
    if not period:
        fail("Falta o mês de referência do extrato Banco do Brasil.")
    month, year = MONTHS.index(period[1])+1, int(period[2])
    signed = re.compile(rf"({NUMBER})\s*([CD])(?:\s*\2)?", re.I)
    for page in extraction.pages:
        counters = re.findall(r"PAG\.\s*(\d+)", upper(" ".join(line.text for line in page.lines)))
        if counters != [str(page.page_number)]:
            fail("Não foi possível conferir a sequência de páginas Banco do Brasil.")
        columns = _headers(page, ("DATA", "ORIGEM", "HISTORICO", "DOCUMENTO", "LOTE", "VALOR R$"))
        if not columns:
            fail("Falta um cabeçalho de colunas do Banco do Brasil.")
        top = next(line.bbox["top"] for line in page.lines if line.bbox and upper(line.text) == "DATA")
        anchors = sorted([line for line in page.lines if line.bbox and line.bbox["top"] > top
                          and line.bbox["left"] < columns["ORIGEM"]-.02 and re.fullmatch(DATE, line.text)],
                         key=lambda line: line.bbox["top"])
        used = set()
        for index, anchor in enumerate(anchors):
            low = anchor.bbox["top"]-.004
            high = anchors[index+1].bbox["top"]-.004 if index+1 < len(anchors) else .96
            cells = [line for line in page.lines if line.bbox and low <= line.bbox["top"] < high]
            values = [line for line in cells if line.bbox["left"] >= columns["VALOR R$"]-.04 and signed.fullmatch(line.text)]
            labels = [line for line in cells if columns["HISTORICO"]-.02 <= line.bbox["left"] < columns["DOCUMENTO"]-.08]
            if len(values) != 1 or not labels:
                fail()
            value = values[0]
            used.add(value.id)
            description = " ".join(line.text for line in sorted(labels, key=lambda line: line.line_index))
            if upper(description).startswith("SALDO DO DIA"):
                continue
            refs = [line for line in cells if columns["DOCUMENTO"]-.08 <= line.bbox["left"] < columns["LOTE"]-.02
                    and line.text.isdigit()]
            if len(refs) != 1 or not refs[0].text.isdigit():
                fail()
            match = signed.fullmatch(value.text)
            amount = money(match[1]) * (-1 if match[2].upper() == "D" else 1)
            date = day(anchor.text)
            if date[:7] != f"{year:04d}-{month:02d}":
                fail("Uma data Banco do Brasil está fora do mês de referência.")
            rows.append(_source(_row(page.page_number, anchor.line_index, date, description, amount,
                                    reference=refs[0].text), page, cells))
            candidates += 1
        if used != {line.id for line in page.lines if line.bbox and line.bbox["top"] > top
                    and line.bbox["left"] >= columns["VALOR R$"]-.04 and signed.fullmatch(line.text)}:
            fail("Um valor Banco do Brasil está sem data ou histórico associado.")
    if not rows:
        fail()
    rows.reverse()
    rows[0] = replace(rows[0], warnings=["combined_cash_investment_balance_not_comparable"])
    return VerifiedStatement(rows, BB, None, None, candidate_rows=candidates)


def _sicredi(extraction):
    text = upper("\n".join(line.text for page in extraction.pages for line in page.lines))
    bounds = re.search(rf"PERIODO DE\s+({DATE})\s+A\s+({DATE})", text)
    if not bounds:
        fail()
    start, end = day(bounds[1]), day(bounds[2])
    rows, opening, running, checks = [], None, None, 0
    previous_columns = None
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        stop = min((line.bbox["top"] for line in lines if upper(line.text).startswith("LANCAMENTOS FUTUROS")), default=1)
        columns = _headers(page, ("DATA", "DESCRICAO", "DOCUMENTO", "VALOR", "SALDO"))
        if not columns:
            if previous_columns is None:
                fail("Falta um cabeçalho Sicredi para conferir as colunas.")
            dates = [line for line in lines if re.fullmatch(DATE, line.text) and line.bbox["left"] < .3]
            if not dates:
                if stop != 1:
                    break
                fail()
            offset = median(line.bbox["left"] for line in dates)-previous_columns["DATA"]
            if abs(offset) > .08:
                fail()
            columns = {key: value+offset for key, value in previous_columns.items()}
            header_y = 0
        else:
            previous_columns = columns
            header_y = next(line.bbox["top"] for line in lines if upper(line.text) == "DATA")
        boundary = (columns["VALOR"] + columns["SALDO"])/2
        body = [line for line in lines if header_y < line.bbox["top"] < stop]
        slope = column_slope(body, columns["VALOR"]-.045, boundary)
        def y(line):
            return line.bbox["top"]-slope*line.bbox["left"]
        used = set()
        seeds = [line for line in body if upper(line.text) == "SALDO ANTERIOR"]
        if seeds:
            if opening is not None or len(seeds) != 1:
                fail()
            near = [line for line in body if abs(y(line)-y(seeds[0])) < .006]
            seed = _one(near, boundary, 1)
            opening = running = _number(seed)
            used.add(seed.id)
        anchors = sorted([line for line in body if line.bbox["left"] < columns["DESCRICAO"]-.02
                          and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"])
        for index, anchor in enumerate(anchors):
            low = (y(anchors[index-1])+y(anchor))/2 if index else y(anchor)-.01
            high = (y(anchor)+y(anchors[index+1]))/2 if index+1 < len(anchors) else 1
            cells = [line for line in body if low <= y(line) < high]
            value = _one(cells, columns["VALOR"]-.045, boundary)
            balance = _one(cells, boundary, 1, allow_glyph=True)
            amount, date = _number(value), day(anchor.text)
            repaired = bool(GLYPH.fullmatch(balance.text))
            if repaired:
                words = [word for word in page.words if word.id in balance.word_ids]
                if not words or any(word.text_type != "PRINTED" for word in words):
                    fail("Um saldo Sicredi está ilegível ou contém escrita manual.")
                printed = money("2"+balance.text[1:])
            else:
                printed = _number(balance)
            if running is None or not start <= date <= end or running + amount != printed:
                fail("Os valores Sicredi não fecham com os saldos impressos.")
            descriptions = [line.text for line in cells if columns["DESCRICAO"]-.02 <= line.bbox["left"] < columns["DOCUMENTO"]-.02]
            if not descriptions or rows and date < rows[-1].transaction.date:
                fail()
            row = _source(_row(page.page_number, anchor.line_index, date, " ".join(descriptions), amount, printed), page, cells)
            if repaired:
                row = replace(row, warnings=["ocr_balance_recovered_from_context"])
            rows.append(row)
            used.update((value.id, balance.id))
            running = printed
            checks += 1
        values = {line.id for line in body if line.bbox["left"] >= columns["VALOR"]-.045
                  and (VALUE.fullmatch(line.text) or GLYPH.fullmatch(line.text))}
        if used != values:
            fail("Um valor Sicredi está sem movimento associado.")
        if stop != 1:
            break
    if not rows or opening is None:
        fail()
    return VerifiedStatement(rows, SICREDI, opening, running, printed_balance_checks=checks)


def _caixa(extraction):
    rows, period, running, opening, checks = [], None, None, None, 0
    checkpoints, scopes = {}, []
    for page in extraction.pages:
        text = upper("\n".join(line.text for line in page.lines))
        bounds = re.search(rf"LANCAMENTOS DE\s+({DATE})\s+A\s+({DATE})", text)
        columns = _headers(page, ("LANCAMENTO", "MOVIMENTO", "DOCUMENTO", "HISTORICO", "VALOR", "SALDO"))
        if not bounds or not columns:
            fail("Falta um período ou cabeçalho do extrato Caixa.")
        scope = tuple(sorted(day(date) for date in bounds.groups()))
        if scope != period:
            if scopes and scope[0] <= scopes[-1][1]:
                fail("Os períodos Caixa se sobrepõem ou estão fora de sequência.")
            period, running = scope, None
            scopes.append(scope)
        header_y = max(line.bbox["top"] for line in page.lines if line.bbox and upper(line.text) in {"MOVIMENTO", "SALDO"})
        body = [line for line in page.lines if line.bbox and line.bbox["top"] > header_y]
        anchors = sorted([line for line in body if columns["MOVIMENTO"]-.03 <= line.bbox["left"] < columns["DOCUMENTO"]-.03
                          and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"])
        posting = [line for line in body if line.bbox["left"] < columns["MOVIMENTO"]-.05 and re.fullmatch(DATE, line.text)]
        slopes = []
        for anchor in anchors:
            pairs = sorted(posting, key=lambda line: abs(line.bbox["top"]-anchor.bbox["top"]))
            if pairs and abs(pairs[0].bbox["top"]-anchor.bbox["top"]) < .012:
                slopes.append((pairs[0].bbox["top"]-anchor.bbox["top"])/(pairs[0].bbox["left"]-anchor.bbox["left"]))
        slope = median(slopes) if slopes else 0
        if abs(slope) > .06:
            fail("A inclinação da página Caixa impede a associação segura das colunas.")
        def y(line):
            return line.bbox["top"] - slope * line.bbox["left"]
        width = next(line.bbox["width"] for line in page.lines if line.bbox and upper(line.text) == "VALOR")
        boundary = (columns["VALOR"]+width+columns["SALDO"])/2
        used = set()
        for index, anchor in enumerate(anchors):
            low = (y(anchors[index-1])+y(anchor))/2 if index else y(anchor)-.01
            high = (y(anchor)+y(anchors[index+1]))/2 if index+1 < len(anchors) else 1
            cells = [line for line in body if low <= y(line) < high]
            value = _one(cells, columns["VALOR"]-.025, boundary)
            balance = _one(cells, boundary, 1)
            amount, printed, date = _number(value), _number(balance), day(anchor.text)
            description = " ".join(line.text for line in cells if columns["HISTORICO"]-.03 <= line.bbox["left"] < columns["VALOR"]-.04)
            if not period[0] <= date <= period[1] or not description:
                fail()
            used.update((value.id, balance.id))
            if upper(description) == "SALDO DIA":
                if running is None:
                    running = printed
                    checkpoints[len(rows)] = float(running)
                    if opening is None:
                        opening = running
                elif running != printed:
                    fail("Um saldo diário Caixa não fecha dentro do mesmo período.")
                checks += 1
                continue
            if running is None:
                running = printed - amount
                checkpoints[len(rows)] = float(running)
                if opening is None:
                    opening = running
            if running + amount != printed:
                fail("Os valores Caixa não fecham dentro do mesmo período.")
            rows.append(_source(_row(page.page_number, anchor.line_index, date, description, amount, printed), page, cells))
            running = printed
            checks += 1
        values = {line.id for line in body if line.bbox["left"] >= columns["VALOR"]-.025 and VALUE.fullmatch(line.text)}
        if used != values:
            fail("Um valor Caixa está sem data de movimento associada.")
    if not rows:
        fail()
    return VerifiedStatement(rows, CAIXA, opening, running, opening_inferred=True, partial=len(scopes)>1,
                             printed_balance_checks=checks, balance_checkpoints=checkpoints)
