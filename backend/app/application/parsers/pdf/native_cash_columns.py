"""Native Bradesco/Santander cash ledgers, delimited by their printed controls."""
import re
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.errors import OcrFinancialAmbiguityError
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.layout_specific.santander_ocr import SANTANDER_OCR_LAYOUT, _period
from app.application.parsers.pdf.native_itau_monthly import _boxes
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK, _read_history, _TextBox
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.text_extraction import read_layout_native_pdf_page_texts
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, fail, money
from app.application.source_regions import SourceRegion

BRADESCO_UNIFIED = "bradesco_extrato_unificado_conta_corrente_native_v1"
BRADESCO_MOVEMENTS = "bradesco_extrato_movimentacao_periodo_native_v1"
BRADESCO_MONTHLY = "bradesco_net_empresa_extrato_mensal_por_periodo_v1"
_MONEY = re.compile(rf"(?<![\w.,])[+-]?{NUMBER}-?(?![\d,.])")
_DATE = re.compile(r"(?<!\d)\d{2}/\d{2}(?:/\d{2}(?:\d{2})?)?(?!\d)")
_MESSAGE = "Não foi possível conferir todas as colunas, os lançamentos e os totais da conta corrente. Envie o extrato original completo."


def _fail():
    fail(_MESSAGE)


def _kind(pages):
    text = upper("\n".join(pages))
    header = upper(pages[0]) if pages else ""
    compact = re.sub(r"\s+", "", text)
    if all(marker in compact for marker in ("EXTRATOMENSAL/PORPERIODO", "EXTRATODE:", "LANCAMENTO", "DCTO.", "SALDOANTERIOR")):
        return BRADESCO_MONTHLY
    if ("EXTRATO UNIFICADO - PESSOA JURIDICA" in header and "BRADESCO" in text
            and re.search(r"\bDOCTO\b", text)):
        return BRADESCO_UNIFIED
    if ("MOVIMENTACAO DO PERIODO - CONTA CORRENTE" in header
            and ("BRADESCO" in text or "RENTAB.INVEST FACILCRED" in text)):
        return BRADESCO_MOVEMENTS
    if ("EXTRATO CONSOLIDADO INTELIGENTE" in text and "SANTANDER" in text
            and "EXTRATO_PJ_A4_INTELIGENTE" in text and "SALDO DE CONTA CORRENTE EM" in text):
        return SANTANDER_OCR_LAYOUT
    return None


def _columns(tp, text, santander):
    patterns = [r"\bData\b", r"Descri[cç][aã]o" if santander else r"Hist[oó]rico|Lan[cç]amento",
                r"Documento" if santander else r"Docto|Dcto\.",
                r"Cr[eé]ditos?", r"D[eé]bitos?", r"Saldo(?: \(R\$\))?"]
    choices = [_boxes(tp, text, re.compile(pattern, re.IGNORECASE)) for pattern in patterns]
    for anchor in choices[0]:
        found = [anchor]
        for index, candidates in enumerate(choices[1:], 1):
            tolerance = 20 if santander and index in (3, 4) else 4
            near = [box for box in candidates if box.left > found[-1].right and abs(box.bottom-anchor.bottom) < tolerance]
            if not near:
                break
            found.append(min(near, key=lambda box: abs(box.bottom-anchor.bottom)))
        if len(found) == 6:
            return found
    return None


def _value(raw):
    return money(raw.strip("+-")) * (-1 if "-" in raw else 1)


def _date(raw, year):
    try:
        if len(raw) == 5:
            return datetime.strptime(raw+f"/{year}", "%d/%m/%Y").date()
        return datetime.strptime(raw, "%d/%m/%Y" if len(raw) == 10 else "%d/%m/%y").date()
    except ValueError:
        _fail()


def _summary(text):
    try:
        start, end = _period(text)
    except OcrFinancialAmbiguityError:
        _fail()
    result = {}
    for key, label in {
        "opening": "SALDO DE CONTA CORRENTE EM " + (start-timedelta(days=1)).strftime("%d/%m"),
        "closing": "SALDO DE CONTA CORRENTE EM " + end.strftime("%d/%m"),
        "credit": "TOTAL DE CREDITOS", "debit": "TOTAL DE DEBITOS",
    }.items():
        values = {_value(m[1]) for m in re.finditer(re.escape(label)+rf"\s+([+-]?{NUMBER}-?)(?![\d,.])", upper(text))}
        if len(values) != 1:
            _fail()
        result[key] = values.pop()
    if (min(result["credit"], result["debit"]) < 0
            or result["opening"]+result["credit"]-result["debit"] != result["closing"]):
        financial_mismatch(_MESSAGE)
    return start, end, result


def parse_santander_inactive_cash(pages):
    """Two cash boundary rows with no movements; investments stay outside this scope."""
    text = upper("\n".join(pages))
    if _kind(pages) != SANTANDER_OCR_LAYOUT:
        return None
    try:
        start, end = _period(text)
    except OcrFinancialAmbiguityError:
        return None
    section = re.search(
        r"CONTA CORRENTE\s+(?:MOVIMENTACAO\s+)?DATA DESCRICAO .*?SALDO \(R\$\)"
        r"(?:\s+CREDITOS DEBITOS)?\s+(.*?)\s+SALDOS POR PERIODO", text)
    if not section:
        return None
    previous = (start-timedelta(days=1)).strftime("%d/%m")
    final = end.strftime("%d/%m")
    body = section[1].split("SE SUA EMPRESA", 1)[0].rstrip(' \"“”\ufffd\x93')
    rows = re.fullmatch(rf"SALDO EM {previous}\s+([+-]?{NUMBER}-?)\s+SALDO EM {final}\s+([+-]?{NUMBER}-?)", body)
    if not rows or _value(rows[1]) != _value(rows[2]):
        return None
    balance = _value(rows[1])
    for date in (previous, final):
        matches = re.findall(rf"SALDO DE CONTA CORRENTE EM {date}\s+([+-]?{NUMBER}-?)", text)
        if len(matches) != 1 or _value(matches[0]) != balance:
            return None
    return VerifiedStatement([], SANTANDER_OCR_LAYOUT, balance, balance, printed_balance_checks=4)


def _source_lines(tp, text):
    result, offset = [], 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        first = next((i for i, char in enumerate(line) if not char.isspace()), None)
        if first is not None:
            result.append((number, tp.get_charbox(offset+first)[1]))
        offset += len(line)
    return result


def parse_native_cash_columns(raw, page_texts):
    kind = _kind(page_texts)
    if kind is None:
        return None
    inactive = parse_santander_inactive_cash(page_texts)
    if inactive is not None:
        return inactive, tuple(page_texts)
    import pypdfium2 as pdfium

    santander = kind == SANTANDER_OCR_LAYOUT
    start = end = None
    controls = None
    if santander:
        start, end, controls = _summary("\n".join(read_layout_native_pdf_page_texts(raw)))
    elif kind == BRADESCO_UNIFIED:
        period = re.search(r"PERIODO\s+(\d{2}/\d{2}/\d{4})\s+A\s+(\d{2}/\d{2}/\d{4})", upper("\n".join(page_texts)))
        if period is None:
            _fail()
        start, end = _date(period[1], None), _date(period[2], None)
    rows, source_pages, accounts = [], [], set()
    opening = opening_date = printed_closing = printed_totals = current = None
    finished = False
    checks = 0
    previous_columns = None
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for page_index in range(len(document)):
            with closing(document[page_index]) as page, closing(page.get_textpage()) as tp:
                text = "".join(chr(pdfium.raw.FPDFText_GetUnicode(tp, i)) for i in range(tp.count_chars()))
                source_pages.append(text)
                normalized = upper(text)
                accounts.update(re.findall(r"AGENCIA:\s*(\d{3,5})\s+CONTA:\s*([\d.-]+)", normalized))
                if len(accounts) > 1:
                    _fail()
                columns = _columns(tp, text, santander)
                if finished:
                    if columns is not None and kind != BRADESCO_MONTHLY:
                        _fail()
                    continue
                latest_boxes = _boxes(tp, text, re.compile(r"[ÚUuú]ltimos\s+Lan[cç]amentos", re.I)) if kind == BRADESCO_MONTHLY else []
                cutoff = max((box.top for box in latest_boxes), default=0)
                if cutoff and columns is not None and columns[0].top < cutoff:
                    columns = None
                continued_monthly = columns is None and kind == BRADESCO_MONTHLY and previous_columns is not None
                if continued_monthly:
                    columns = previous_columns
                if columns is None:
                    if opening is not None:
                        _fail()
                    continue
                previous_columns = columns
                if page.get_rotation():
                    _fail()
                date_col, history, reference, credit, debit, balance = columns
                if kind == BRADESCO_MONTHLY and opening is None:
                    # A sequential text PDF may print the same header without
                    # placing its values in those columns. Preserve the existing
                    # text reader, including independently balanced sections.
                    openings = _boxes(tp, text, re.compile(r"SALDO\s+ANTERIOR", re.I))
                    first_opening = max(openings, key=lambda box: box.bottom) if openings else None
                    opening_values = [box for box in _boxes(tp, text, _MONEY)
                                      if first_opening and abs(box.bottom-first_opening.bottom) < 3]
                    if not any((box.left+box.right)/2 >= (debit.right+balance.left)/2 for box in opening_values):
                        return None
                    periods = set(re.findall(r"ENTRE\s+(\d{2}/\d{2}/\d{4})\s+E\s+(\d{2}/\d{2}/\d{4})", normalized))
                    if len(periods) != 1:
                        _fail()
                    first, last = next(iter(periods))
                    start, end = _date(first, None), _date(last, None)
                    if start > end:
                        _fail()
                characters = [_TextBox(char, *tp.get_charbox(i, loose=True)) for i, char in enumerate(text)
                              if char not in {"\r", "\n", "\x00"}]

                def bounded(left, bottom, right, top):
                    return " ".join(_read_history(characters, left, bottom, right, top).split())

                header_bottom = page.get_height() if continued_monthly else min(box.bottom for box in columns)
                if continued_monthly:
                    # Repeated identity text above a top-of-page Folha counter
                    # is not part of a continued ledger's document column.
                    counters = [box for box in _boxes(tp, text, re.compile(r"\bFolha\s+\d+\s*/\s*\d+\b", re.I))
                                if box.bottom > page.get_height() * .75]
                    if len(counters) > 1:
                        _fail()
                    if counters:
                        header_bottom = min(header_bottom, counters[0].bottom)
                history_left = date_col.right+5
                boundary = (credit.right+debit.left)/2, (debit.right+balance.left)/2
                values = [box for box in _boxes(tp, text, _MONEY)
                          if box.left > reference.right+3 and box.top < header_bottom-1 and box.bottom > cutoff]
                dates = [box for box in _boxes(tp, text, _DATE)
                         if box.left < date_col.right+5 and box.right < reference.left-5 and box.top < header_bottom-1]
                if dates:
                    history_left = max(history_left, max(box.right for box in dates)+3)
                groups = []
                for box in sorted(values, key=lambda item: -item.bottom):
                    if groups and abs(groups[-1][0].bottom-box.bottom) < 2:
                        groups[-1].append(box)
                    else:
                        groups.append([box])
                references = [box for box in _boxes(tp, text, re.compile(r"(?<!\w)\d{1,12}(?!\w)"))
                              if reference.left-8 <= box.left and box.right < credit.left-3
                              and box.top < header_bottom-1 and box.bottom > cutoff]
                used_references = set()
                lines = _source_lines(tp, text)
                width, height = page.get_size()
                for index, cells in enumerate(groups):
                    anchor = cells[0]
                    label = upper(bounded(date_col.left-3, anchor.bottom-1, reference.left-3, anchor.top+1))
                    amounts = {}
                    for box in cells:
                        center = (box.left+box.right)/2
                        role = "credit" if center < boundary[0] else "debit" if center < boundary[1] else "balance"
                        if role in amounts:
                            _fail()
                        amounts[role] = _value(box.text)
                    available_dates = [box for box in dates if box.bottom >= anchor.bottom-2]
                    if available_dates:
                        date = min(available_dates, key=lambda box: box.bottom).text
                        year = end.year if end else current.year if current else None
                        if len(date) == 5 and year is None:
                            _fail()
                        if end and len(date) == 5 and int(date[3:5]) > end.month:
                            year -= 1
                        current = _date(date, year)
                    opening_label = ("SALDO ANTERIOR" in label
                                     or santander and label == "SALDO EM " + (start-timedelta(days=1)).strftime("%d/%m"))
                    if opening_label:
                        if opening is not None or set(amounts) != {"balance"}:
                            _fail()
                        opening = amounts["balance"]
                        if santander and opening != controls["opening"]:
                            financial_mismatch(_MESSAGE)
                        if not santander and current is None and kind != BRADESCO_MONTHLY:
                            _fail()
                        opening_date = start-timedelta(days=1) if santander else current
                        if start and opening_date is not None and opening_date >= start:
                            _fail()
                        continue
                    terminal = (label == "SALDO EM " + end.strftime("%d/%m") if santander
                                else label in {"TOTAL", "TOTAL DA MOVIMENTACAO"})
                    if terminal:
                        expected_roles = {"balance"} if santander else {"credit", "debit", "balance"}
                        if set(amounts) != expected_roles:
                            _fail()
                        printed_closing = amounts["balance"]
                        printed_totals = ((controls["credit"], controls["debit"]) if santander
                                          else (amounts["credit"], abs(amounts["debit"])))
                        finished = True
                        break
                    if opening is None:
                        _fail()
                    if set(amounts) == {"balance"}:
                        if not rows or label:
                            _fail()
                        previous = rows[-1]
                        if previous.running_balance is not None and Decimal(str(previous.running_balance)) != amounts["balance"]:
                            _fail()
                        source = min(lines, key=lambda value: abs(value[1]-anchor.bottom))[0]
                        region = SourceRegion(page_index+1, anchor.left/width, (height-anchor.top-1)/height,
                                              (anchor.right-anchor.left)/width, (anchor.top-anchor.bottom+2)/height)
                        rows[-1] = replace(previous, running_balance=float(amounts["balance"]),
                            source_regions=[*previous.source_regions, region],
                            source_line_refs=(*previous.source_line_refs, (page_index+1, source)))
                        continue
                    if len(set(amounts) & {"credit", "debit"}) != 1 or current is None:
                        _fail()
                    if (opening_date is not None and current <= opening_date
                            or rows and current.isoformat() < rows[-1].transaction.date):
                        _fail()
                    if start and not start <= current <= end:
                        if kind == BRADESCO_MONTHLY:
                            financial_mismatch(_MESSAGE)
                        else:
                            _fail()
                    if kind == BRADESCO_MONTHLY:
                        # A wrapped history can start above or continue below
                        # the amount baseline; use adjacent monetary row bands.
                        top = (groups[index-1][0].bottom+anchor.bottom)/2 if index else header_bottom-1
                        bottom = (groups[index+1][0].bottom+anchor.bottom)/2 if index+1 < len(groups) else max(0, anchor.bottom-12)
                    else:
                        top = anchor.top+1
                        bottom = groups[index+1][0].top+1 if index+1 < len(groups) else max(0, anchor.bottom-12)
                    description = bounded(history_left, bottom, reference.left-3, top)
                    same_line = bounded(history_left, anchor.bottom-1, reference.left-3, anchor.top+1)
                    if not description or "SALDO" in upper(description[:6]):
                        _fail()
                    refs = [box for box in references if abs(box.bottom-anchor.bottom) < 3]
                    if len(refs) > 1:
                        _fail()
                    if not same_line and (kind != BRADESCO_MONTHLY or len(refs) != 1):
                        _fail()
                    used_references.update(id(box) for box in refs)
                    if "credit" in amounts and amounts["credit"] < 0:
                        _fail()
                    if any(box.text.startswith("+") and (box.left+box.right)/2 >= boundary[0] for box in cells):
                        _fail()
                    amount = amounts["credit"] if "credit" in amounts else -abs(amounts["debit"])
                    source = min(lines, key=lambda value: abs(value[1]-anchor.bottom))[0]
                    region = SourceRegion(page_index+1, history_left/width, max(0, height-top-2)/height,
                        (max(box.right for box in cells)-history_left)/width, (top+2-bottom)/height)
                    row = _row(page_index+1, source, current.isoformat(), description, amount, amounts.get("balance"),
                               refs[0].text if refs else None)
                    rows.append(replace(row, source_regions=[region]))
                terminal_y = anchor.bottom if finished else 0
                if any(id(box) not in used_references and box.bottom > terminal_y for box in references):
                    _fail()
    if not finished or not rows or opening is None:
        _fail()
    running = opening
    for row in rows:
        running += Decimal(str(row.transaction.amount))
        if row.running_balance is not None:
            if Decimal(str(row.running_balance)) != running:
                financial_mismatch(_MESSAGE)
            checks += 1
    totals = (sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0)),
              sum((-Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0)))
    if running != printed_closing or totals != printed_totals or santander and running != controls["closing"]:
        financial_mismatch(_MESSAGE)
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=checks, candidate_rows=len(rows)), tuple(source_pages)
