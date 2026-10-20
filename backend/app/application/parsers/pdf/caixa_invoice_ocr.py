"""CAIXA's two-column card invoice: financial sections and C/D controls only."""
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import VerifiedStatement, _row, fail
from app.application.source_regions import evidence_regions

LAYOUT = "caixa_fatura_cartao_visa_ocr_v1"
_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+,\d{2}|\d+[,.]\d{2})"
_SIGNED = re.compile(rf"(?:R\$\s*)?({_NUMBER})\s*([CD])", re.IGNORECASE)
_MESSAGE = "Não foi possível conferir os lançamentos e os subtotais da fatura Caixa. Envie a fatura original completa."


def _fail():
    fail(_MESSAGE)


def is_caixa_invoice(extraction):
    text = upper(" ".join(line.text for page in extraction.pages for line in page.lines))
    return "CAIXA" in text and "VALOR TOTAL DESTA FATURA" in text and ("VISA" in text or "CARTOES" in text)


def _one(values):
    if len(values) != 1:
        _fail()
    return values[0]


def _decimal(value):
    return Decimal(value.replace(".", "").replace(",", ".")) if "," in value else Decimal(value)


def _signed(text):
    match = _SIGNED.fullmatch(text.strip())
    if not match:
        _fail()
    return _decimal(match[1]) * (1 if match[2].upper() == "C" else -1)


def _near(line, other):
    return abs(line.bbox["top"]-other.bbox["top"]) < .0025


def _printed(page, lines):
    ids = {word_id for line in lines for word_id in line.word_ids}
    if any(word.id in ids and word.text_type == "HANDWRITING" for word in page.words):
        _fail()


def _below(page, label, pattern):
    candidates = [line for line in page.lines if line.bbox and pattern.fullmatch(line.text.strip())
                  and label.bbox["top"] < line.bbox["top"] < label.bbox["top"]+.06
                  and line.bbox["left"] >= label.bbox["left"]-.02]
    if not candidates:
        _fail()
    top = min(line.bbox["top"] for line in candidates)
    return _one([line for line in candidates if abs(line.bbox["top"]-top) < .0025])


def _cover(extraction):
    first = extraction.pages[0]
    due_label = _one([line for line in first.lines if upper(line.text) == "VENCIMENTO" and line.bbox])
    due_cell = _below(first, due_label, re.compile(r"\d{2}/\d{2}/\d{4}"))
    try:
        day, month, year = map(int, due_cell.text.split("/"))
        due = date(year, month, day)
    except ValueError:
        _fail()
    total_label = _one([line for line in first.lines if upper(line.text) == "VALOR TOTAL DESTA FATURA" and line.bbox])
    total_cell = _below(first, total_label, re.compile(rf"R\$\s*{_NUMBER}"))
    _printed(first, [due_label, due_cell, total_label, total_cell])
    total = _decimal(total_cell.text.replace("R$", "").strip())
    return due, total, [due_label, due_cell, total_label, total_cell]


def _transaction_date(raw, due):
    try:
        day, month = map(int, raw.split("/"))
        year = due.year-int((month, day) > (due.month, due.day))
        return date(year, month, day).isoformat()
    except ValueError:
        _fail()


def parse_caixa_invoice(extraction):
    if [page.page_number for page in extraction.pages] != list(range(1, len(extraction.pages)+1)):
        _fail()
    due, due_amount, cover_sources = _cover(extraction)
    rows, opening, closing = [], None, None
    card = section = None
    card_expenses = Decimal(0)
    section_expenses = Decimal(0)
    card_open = section_open = False
    checks = 0
    financial_pages = 0
    for page in extraction.pages[1:]:
        labels = [line for line in page.lines if upper(line.text) == "DEMONSTRATIVO" and line.bbox]
        if closing is not None or not labels:
            _fail()
        financial_pages += 1
        title = _one(labels)
        headers = [line for line in page.lines if line.bbox and upper(line.text).startswith("CREDITO/DEBITO")
                   and title.bbox["top"] < line.bbox["top"] < title.bbox["top"]+.05]
        value_header = _one(headers)
        desc_header = _one([line for line in page.lines if line.bbox and upper(line.text) == "DESCRICAO" and _near(line, value_header)])
        city_headers = [line for line in page.lines if line.bbox and upper(line.text) == "CIDADE/PAIS" and _near(line, value_header)]
        left, value_left = title.bbox["left"]-.012, value_header.bbox["left"]-.025
        desc_right = _one(city_headers).bbox["left"]-.005
        dates_right = desc_header.bbox["left"]-.005
        body = [line for line in page.lines if line.bbox and line.bbox["left"] >= left
                and line.bbox["top"] > value_header.bbox["top"]+.006]
        if any(not line.bbox and _SIGNED.fullmatch(line.text.strip()) for line in page.lines):
            _fail()
        body.sort(key=lambda line: (line.bbox["top"], line.bbox["left"]))
        used = set()
        for anchor in body:
            if anchor.id in used or anchor.bbox["left"] >= value_left:
                continue
            label = upper(anchor.text)
            if label.startswith("VALOR TOTAL DESTA FATURA"):
                _printed(page, [anchor])
                matches = list(_SIGNED.finditer(anchor.text))
                closing = _signed(_one(matches)[0])
                if closing != -due_amount or card_open or section_open:
                    _fail()
                checks += 1
                break
            near = [line for line in body if _near(line, anchor)]
            amounts = [line for line in near if line.bbox["left"] >= value_left]
            if label.startswith("TOTAL FINAL (CARTAO"):
                _printed(page, near)
                if section_open or not card_open or -_signed(_one(amounts).text) != card_expenses:
                    _fail()
                used.update(line.id for line in near)
                card_open = False
                card_expenses = Decimal(0)
                section = None
                checks += 1
                continue
            if label.startswith("TOTAL COMPRAS"):
                _printed(page, near)
                expected = "installments" if label.startswith("TOTAL COMPRAS PARCELADAS") else "purchases"
                if section != expected or not section_open or -_signed(_one(amounts).text) != section_expenses:
                    _fail()
                used.update(line.id for line in near)
                section_open = False
                section_expenses = Decimal(0)
                section = None
                checks += 1
                continue
            if (label in {"TOTAL", "DATA", "DESCRICAO", "CIDADE/PAIS", "VALOR U$$", "VALOR ORIGINAL", "COTACAO"}
                    or label.startswith("CREDITO/DEBITO")):
                used.update(line.id for line in near)
                continue
            card_match = re.search(r"\(CARTAO\s+(\d{4})\)", label)
            if card_match:
                if section_open:
                    _fail()
                if card_open and card != card_match[1]:
                    _fail()
                card = card_match[1]
                card_open = True
                if label.startswith("COMPRAS"):
                    section = "installments" if label.startswith("COMPRAS PARCELADAS") else "purchases"
                    section_open = True
                used.add(anchor.id)
                continue
            if label == "ANUIDADE":
                if not card_open or section_open:
                    _fail()
                section = "annuity"
                used.add(anchor.id)
                continue
            if not amounts:
                if (anchor.bbox["left"] < dates_right and re.fullmatch(r"\d{2}/\d{2}", anchor.text.strip())
                        or section == "annuity" and label.startswith("ANUIDADE ")):
                    _fail()
                # Contactless icons can precede the date on the same printed row.
                continue
            dates = [line for line in near if line.bbox["left"] < dates_right and re.fullmatch(r"\d{2}/\d{2}", line.text.strip())]
            descriptions = [line for line in near if (left if section == "annuity" else dates_right) <= line.bbox["left"] < desc_right
                            and not re.fullmatch(r"\d{2}/\d{2}", line.text.strip())]
            description = " ".join(line.text for line in sorted(descriptions, key=lambda line: line.bbox["left"]))
            if not description:
                _fail()
            _printed(page, near)
            amount = _signed(_one(amounts).text)
            used.update(line.id for line in near)
            if upper(description) == "TOTAL DA FATURA ANTERIOR":
                if opening is not None or rows or amount > 0:
                    _fail()
                opening = amount
                continue
            if opening is None:
                _fail()
            warnings = []
            if not dates and section == "annuity" and upper(description).startswith("ANUIDADE"):
                tx_date = due.isoformat()
                description += " (sem data impressa; vencimento da fatura)"
                warnings.append("invoice_due_date_used_for_undated_fee")
            else:
                tx_date = _transaction_date(_one(dates).text, due)
            if section in {"annuity", "purchases", "installments"}:
                if not card_open or amount > 0:
                    _fail()
                card_expenses -= amount
                if section in {"purchases", "installments"}:
                    if not section_open:
                        _fail()
                    section_expenses -= amount
                description += f" CARTÃO FINAL {card}"
            row = _row(page.page_number, anchor.line_index, tx_date, description, amount)
            regions = evidence_regions(page.page_number, near, words=page.words)
            refs = tuple((page.page_number, line.line_index) for line in near)
            if warnings:
                regions += evidence_regions(extraction.pages[0].page_number, cover_sources, words=extraction.pages[0].words)
                refs += tuple((extraction.pages[0].page_number, line.line_index) for line in cover_sources)
            rows.append(replace(row, warnings=warnings, source_regions=regions, source_line_refs=refs))
        if closing is not None:
            # A monetary C/D cell inside the consumed financial region cannot disappear.
            if any(line.id not in used and line.bbox["left"] >= value_left and _SIGNED.fullmatch(line.text.strip())
                   and line.bbox["top"] < anchor.bbox["top"] for line in body):
                _fail()
    if (not financial_pages or not rows or opening is None or closing is None
            or opening+sum((Decimal(str(row.transaction.amount)) for row in rows), Decimal(0)) != closing):
        _fail()
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks, candidate_rows=len(rows))
