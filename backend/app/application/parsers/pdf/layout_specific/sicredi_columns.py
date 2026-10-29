"""Native Sicredi columns and explicit daily totals, with printed ledger controls."""
import calendar
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.application.errors import InvalidFileContentError, PdfFinancialValidationError
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _PdfLine
from app.application.parsers.pdf.statement_review import financial_mismatch

MATRIX_LAYOUT = "sicredi_matricial_paisagem_conta_corrente_v1"
HTML_LAYOUT = "sicredi_extrato_html_totais_diarios_v1"
MATRIX_PARSER = "layout_specific_sicredi_matrix"
HTML_PARSER = "layout_specific_sicredi_html"
_DATE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})(?:\s+(.*))?$")
_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_CURRENCY = re.compile(rf"[+\-−]?\s*R\$\s*{_NUMBER}")
_MESSAGE = "Não foi possível conferir as colunas, os totais e os saldos do extrato Sicredi. Confira se o PDF está completo e legível."


def _fail():
    raise PdfFinancialValidationError(_MESSAGE)


def _money(text):
    return Decimal(str(parse_pdf_amount(text)))


def _date(text):
    try:
        return parse_row_date(text, fallback_year=None)
    except InvalidFileContentError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _single(values):
    if len(values) != 1:
        _fail()
    return next(iter(values))


def _matrix(lines):
    # The printable summary can share a line with a separately ruled forecast
    # panel. Its amounts and dates are not movements or controls of this ledger.
    texts = [normalize_upper_text(line.text) for line in lines]
    summaries = {}
    for kind, prefix in (("debits", "DEBITOS"), ("credits", "CREDITOS"), ("closing", "SALDO ATUAL")):
        amounts = {_money(token.value) for line, text in zip(lines, texts, strict=True) if text.startswith(prefix)
                   for token in find_amount_tokens(line.text.partition("|")[0])}
        if not amounts:
            if kind == "closing":
                return None  # Other matrix templates keep their existing parser.
            continue  # Some matrix statements print closing/carry balances, but no period totals.
        summaries[kind] = _single(amounts)
    headers = [line for line in lines if re.search(r"\bDATA\s+DOCUMENTO\s+HISTORICO\s{2,}DEBITO\s+CREDITO\s+SALDO", line.text)]
    if not headers:
        return None  # A native layout-text candidate must retain column spacing.
    periods = {(int(match[1]), int(match[2]), int(match[3]), int(match[4])) for text in texts
               if (match := re.fullmatch(r"PERIODO: DE (\d{2})/(\d{4}) A (\d{2})/(\d{4})", text))}
    start_month, start_year, end_month, end_year = _single(periods)
    try:
        start = date(start_year, start_month, 1).isoformat()
        end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1]).isoformat()
    except ValueError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc
    opening_values = {_money(token.value) for line, text in zip(lines, texts, strict=True)
                      if "SALDOANTERIOR" in text.replace(" ", "") for token in find_amount_tokens(line.text)}
    opening = _single(opening_values)
    running = opening
    rows = []
    header = None
    for line, text in zip(lines, texts, strict=True):
        if line in headers:
            header = line.text
            continue
        if "DETRANSPORTE" in text.replace(" ", ""):
            if _single({_money(token.value) for token in find_amount_tokens(line.text)}) != running:
                financial_mismatch(_MESSAGE)
        match = _DATE.fullmatch(line.text)
        if match is None:
            continue
        if header is None:
            _fail()
        day = _date(match[1])
        if not start <= day <= end or rows and day < rows[-1].transaction.date:
            _fail()
        debit = re.search(r"\bDEBITO\b", header)
        credit = re.search(r"\bCREDITO\b", header)
        balance = re.search(r"\bSALDO\b", header)
        credit_left, balance_left = (debit.end() + credit.end()) / 2, (credit.end() + balance.end()) / 2
        values = {}
        tokens = find_amount_tokens(line.text)
        for token in tokens:
            center = (token.start + token.end) / 2
            kind = "debit" if center < credit_left else "credit" if center < balance_left else "balance"
            if kind in values or token.start < re.search(r"\bHISTORICO\b", header).end():
                _fail()
            values[kind] = _money(token.value)
        if len(set(values) & {"debit", "credit"}) != 1:
            _fail()
        if values.get("credit", Decimal(0)) < 0:
            _fail()
        amount = -abs(values["debit"]) if "debit" in values else abs(values["credit"])
        running += amount
        if "balance" in values and values["balance"] != running:
            financial_mismatch(_MESSAGE)
        description = line.text[len(match[1]) + len(line.text) - len(line.text.lstrip()):tokens[0].start].strip()
        rows.append(build_parsed_transaction(date=day, description=" ".join(description.split()), amount=float(amount),
            running_balance=float(values["balance"]) if "balance" in values else None,
            source_page=line.page_number, source_line=line.line_number, has_explicit_amount_sign=True,
            source_line_refs=((line.page_number, line.line_number),)))
    debits = -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    credits = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    if not rows:
        _fail()
    if (running != summaries["closing"]
            or "debits" in summaries and debits != summaries["debits"]
            or "credits" in summaries and credits != summaries["credits"]):
        financial_mismatch()
    return rows


def _html(lines):
    texts = [normalize_upper_text(line.text) for line in lines]
    periods = {(_date(match[1]), _date(match[2])) for text in texts
               if (match := re.fullmatch(r"EXTRATO SICREDI DO PERIODO DE (\d{2}/\d{2}/\d{4}) A (\d{2}/\d{2}/\d{4})", text))}
    start, end = _single(periods)
    bounds = {}
    for kind, label in (("opening", "SALDO INICIAL"), ("closing", "SALDO FINAL")):
        bounds[kind] = _single({_money(match.group()) for line, text in zip(lines, texts, strict=True) if text.startswith(label)
                               for match in _CURRENCY.finditer(line.text)})
    rows = []
    pending = []
    day = None
    daily_in = daily_out = Decimal(0)
    running = bounds["opening"]
    closed = False

    def flush():
        nonlocal pending, daily_in, daily_out
        if not pending:
            return
        raw = " ".join(line.text.strip() for line in pending)
        matches = list(_CURRENCY.finditer(raw))
        if len(matches) != 1 or raw[matches[0].end():].strip():
            _fail()
        amount = _money(matches[0].group())
        description = raw[len(raw.split()[0]):matches[0].start()].strip()
        if not description:
            _fail()
        daily_in += max(amount, Decimal(0))
        daily_out += max(-amount, Decimal(0))
        source = pending[0]
        rows.append(build_parsed_transaction(date=day, description=description, amount=float(amount),
            source_page=source.page_number, source_line=source.line_number, has_explicit_amount_sign=True,
            source_line_refs=tuple((line.page_number, line.line_number) for line in pending)))
        pending = []

    for line, text in zip(lines, texts, strict=True):
        if text.startswith("TOTAL"):
            flush()
            amounts = [_money(match.group()) for match in _CURRENCY.finditer(line.text)]
            if len(amounts) < 2:
                _fail()
            incoming = sum((amount for amount in amounts[:-1] if amount > 0), Decimal(0))
            outgoing = -sum((amount for amount in amounts[:-1] if amount < 0), Decimal(0))
            if text.startswith("TOTAL DO DIA"):
                if not rows or day is None:
                    _fail()
                if (incoming, outgoing, amounts[-1]) != (daily_in, daily_out, running + daily_in - daily_out):
                    financial_mismatch()
                row = rows[-1]
                rows[-1] = replace(row, running_balance=float(amounts[-1]),
                    source_line_refs=(*row.source_line_refs, (line.page_number, line.line_number)))
                running, day, daily_in, daily_out = amounts[-1], None, Decimal(0), Decimal(0)
            else:
                credits = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
                debits = -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
                if day is not None:
                    _fail()
                if ((incoming, outgoing, amounts[-1]) != (credits, debits, bounds["closing"]) or running != bounds["closing"]):
                    financial_mismatch()
                closed = True
            continue
        match = _DATE.fullmatch(line.text)
        if match:
            if closed:
                _fail()
            new_day = _date(match[1])
            if not start <= new_day <= end or day is not None and new_day != day or rows and new_day < rows[-1].transaction.date:
                _fail()
            flush()
            day = new_day
            pending = [line]
        elif pending:
            pending.append(line)
    if pending or not closed:
        _fail()
    return rows


@dataclass(frozen=True, slots=True)
class SicrediColumnLayoutParser:
    layout_names: frozenset[str] = frozenset({MATRIX_LAYOUT, HTML_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult | None:
        if not context.is_native_text:
            return None
        rows = _matrix(lines) if layout_name == MATRIX_LAYOUT else _html(lines)
        if rows is None:
            return None
        return LayoutSpecificParseResult(rows, MATRIX_PARSER if layout_name == MATRIX_LAYOUT else HTML_PARSER,
                                        "sicredi_native_columns_and_printed_financial_controls_validated")
