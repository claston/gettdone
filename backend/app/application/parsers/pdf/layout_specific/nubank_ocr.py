"""Nubank OCR: printed amount column, daily sections and statement controls."""
import re
from dataclasses import dataclass, replace
from decimal import Decimal

from app.application.document_extraction_models import ExtractedLine
from app.application.errors import InvalidFileContentError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.normalization.date import FULL_MONTH_PATTERN, parse_statement_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _PdfLine
from app.application.source_regions import evidence_regions

NUBANK_LAYOUT = "nubank_statement_ptbr"
_MONEY = re.compile(r"^[+\-−]?\s*(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}$")
_DATE = re.compile(r"^\d{1,2} [A-Z]{3} \d{4}$")
_PERIOD = re.compile(rf"^(\d{{1,2}} DE (?:{FULL_MONTH_PATTERN}) DE \d{{4}}) A (\d{{1,2}} DE (?:{FULL_MONTH_PATTERN}) DE \d{{4}})$")
_SUMMARY = {"SALDO INICIAL", "SALDO FINAL DO PERIODO", "TOTAL DE ENTRADAS", "TOTAL DE SAIDAS"}
_MESSAGE = "Não foi possível conferir todas as linhas, os totais e os saldos do extrato Nubank. Confira se o PDF está completo e legível."


def _money(text):
    return Decimal(re.sub(r"\s+", "", text.replace("R$", "").replace("−", "-")).replace(".", "").replace(",", "."))


def _date(text):
    try:
        return parse_statement_date(text, fallback_year=None)
    except InvalidFileContentError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _summary(extraction):
    page = extraction.pages[0]
    texts = [line.text.strip() for line in sorted(page.lines, key=lambda line: line.line_index)]
    try:
        stop = next(index for index, text in enumerate(texts) if normalize_upper_text(text) == "MOVIMENTACOES")
    except StopIteration as exc:
        raise OcrFinancialAmbiguityError(_MESSAGE) from exc
    values = {key: set() for key in _SUMMARY}
    for index, text in enumerate(texts[:stop]):
        key = normalize_upper_text(text)
        if key in _SUMMARY and index + 1 < stop and _MONEY.fullmatch(texts[index + 1]):
            values[key].add(_money(texts[index + 1]))
    periods = {(_date(match[1]), _date(match[2])) for text in texts[:stop]
               if (match := _PERIOD.fullmatch(normalize_upper_text(text)))}
    if any(len(found) != 1 for found in values.values()) or len(periods) != 1:
        raise PdfFinancialValidationError(_MESSAGE)
    opening = next(iter(values["SALDO INICIAL"]))
    closing = next(iter(values["SALDO FINAL DO PERIODO"]))
    inflows = abs(next(iter(values["TOTAL DE ENTRADAS"])))
    outflows = abs(next(iter(values["TOTAL DE SAIDAS"])))
    if opening + inflows - outflows != closing:
        raise PdfFinancialValidationError(_MESSAGE)
    return next(iter(periods)), opening, closing, inflows, outflows


@dataclass(frozen=True)
class _Line:
    page: int
    number: int
    item: ExtractedLine


@dataclass(frozen=True)
class _Event:
    index: int
    kind: str
    label: _Line
    value: _Line | None = None


def _body(extraction):
    stream = []
    for ordinal, page in enumerate(extraction.pages):
        active = False
        number = 1
        for item in sorted(page.lines, key=lambda line: line.line_index):
            source_number = number
            if not item.text:
                continue
            number += len(item.text.splitlines())
            text = normalize_upper_text(item.text)
            if (ordinal == 0 and text == "MOVIMENTACOES") or (ordinal > 0 and text == "VALORES EM R$"):
                active = True
                continue
            if text.startswith(("TEM ALGUMA DUVIDA?", "CASO A SOLUCAO", "EXTRATO GERADO DIA")):
                active = False
            if active:
                stream.append(_Line(page.page_number, source_number, item))
    if not stream:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return stream


def _events(stream, extraction):
    events = []
    values = set()
    for index, source in enumerate(stream):
        item = source.item
        text = normalize_upper_text(item.text)
        if _DATE.fullmatch(text):
            events.append(_Event(index, "date", source))
        if not _MONEY.fullmatch(item.text.strip()):
            continue
        if not item.bbox or item.bbox["left"] < .7:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        labels = [(position, line) for position, line in enumerate(stream) if line.page == source.page
                  and line.item.bbox and .15 <= line.item.bbox["left"] < .35
                  and abs(line.item.bbox["top"] - item.bbox["top"]) <= .006]
        if len(labels) != 1:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        position, label = labels[0]
        page = next(page for page in extraction.pages if page.page_number == source.page)
        if any(word.id in item.word_ids and word.text_type == "HANDWRITING" for word in page.words):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        key = normalize_upper_text(label.item.text)
        kind = {"TOTAL DE ENTRADAS": "inflow", "TOTAL DE SAIDAS": "outflow", "SALDO DO DIA": "balance"}.get(key, "movement")
        events.append(_Event(position, kind, label, source))
        values.add(index)
    events.sort(key=lambda event: event.index)
    if len({event.index for event in events}) != len(events):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return events, values


def _regions(sources, extraction):
    return [region for page in extraction.pages for region in evidence_regions(
        page.page_number, [line.item for line in sources if line.page == page.page_number], words=page.words)]


def _parse(extraction):
    period, opening, closing, inflows, outflows = _summary(extraction)
    stream = _body(extraction)
    events, values = _events(stream, extraction)
    rows = []
    day = None
    section = None
    expected = actual = Decimal(0)
    balance = opening
    daily_net = Decimal(0)
    closed_day = True
    section_source = None

    def check_section():
        if section is not None and actual != expected:
            raise PdfFinancialValidationError(_MESSAGE)

    for offset, event in enumerate(events):
        if event.kind == "date":
            check_section()
            new_day = _date(event.label.item.text)
            if not closed_day or not period[0] <= new_day <= period[1] or (day and new_day <= day):
                raise PdfFinancialValidationError(_MESSAGE)
            day, section, closed_day = new_day, None, False
            daily_net = Decimal(0)
            continue
        if day is None or closed_day:
            raise PdfFinancialValidationError(_MESSAGE)
        amount = _money(event.value.item.text.strip())
        if event.kind in {"inflow", "outflow"}:
            check_section()
            section, expected, actual = event.kind, abs(amount), Decimal(0)
            section_source = (event.label, event.value)
        elif event.kind == "balance":
            check_section()
            if not rows or rows[-1].transaction.date != day or balance + daily_net != amount:
                raise PdfFinancialValidationError(_MESSAGE)
            row = rows[-1]
            sources = (event.label, event.value)
            rows[-1] = replace(row, running_balance=float(amount),
                source_regions=[*row.source_regions, *_regions(sources, extraction)],
                source_line_refs=(*row.source_line_refs, *((line.page, line.number) for line in sources)))
            balance, section, closed_day = amount, None, True
        else:
            if (section is None or amount < 0 and section == "inflow"
                    or section == "outflow" and event.value.item.text.strip().startswith("+")):
                raise PdfFinancialValidationError(_MESSAGE)
            signed = abs(amount) if section == "inflow" else -abs(amount)
            actual += abs(signed)
            daily_net += signed
            stop = events[offset + 1].index if offset + 1 < len(events) else len(stream)
            sources = [source for position, source in enumerate(stream[event.index:stop], start=event.index) if position not in values]
            sources.append(event.value)
            description = " ".join(line.item.text.strip() for line in sources[:-1])
            witnesses = [*sources, *section_source]
            rows.append(build_parsed_transaction(date=day, description=description, amount=float(signed),
                source_page=event.label.page, source_line=event.label.number, has_explicit_amount_sign=True,
                source_regions=_regions(witnesses, extraction), source_line_refs=tuple((line.page, line.number) for line in witnesses)))
    check_section()
    if not rows or not closed_day or balance != closing:
        raise PdfFinancialValidationError(_MESSAGE)
    if (sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0)) != inflows
            or -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0)) != outflows):
        raise PdfFinancialValidationError(_MESSAGE)
    return rows


@dataclass(frozen=True, slots=True)
class NubankOcrLayoutParser:
    layout_names: frozenset[str] = frozenset({NUBANK_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult | None:
        if context.source_extraction is None or not any(normalize_upper_text(line.text) == "MOVIMENTACOES" for line in lines):
            return None
        return LayoutSpecificParseResult(_parse(context.source_extraction), "layout_specific_nubank_ocr",
                                        "nubank_ocr_geometry_daily_sections_and_statement_totals_validated")
