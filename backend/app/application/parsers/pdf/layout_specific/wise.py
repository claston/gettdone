"""Wise PT-BR statements put each transaction's date below its financial row."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NoReturn

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

WISE_CURRENCIES = {"wise_extrato_brl_v1": "BRL", "wise_extrato_usd_v1": "USD"}
_MONTHS = {name: number for number, name in enumerate((
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"
), 1)}
_DATE = r"\d{1,2} DE [A-Z]+ DE \d{4}"
_ZONE = r"\[GMT[+-]\d{2}:\d{2}\]"
_MONEY = r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_PERIOD = re.compile(rf"^({_DATE}) {_ZONE} - ({_DATE}) {_ZONE}$")
_SUMMARY = re.compile(rf"^(BRL|USD) EM ({_DATE}) {_ZONE} ({_MONEY}) (BRL|USD)$")
_ROW = re.compile(rf"^(.+?)\s+({_MONEY})\s+({_MONEY})$")
_TRANSACTION = re.compile(rf"^({_DATE})\s*\|\s*TRANSACAO:\s*([A-Z0-9_-]+)$", re.IGNORECASE)
_PAGE = re.compile(r"^REF:(\S+)\s+(\d+)\s*/\s*(\d+)$", re.IGNORECASE)
_TABLE = "DESCRICAO ENTRADA SAIDA VALOR"
_ERROR = "Não foi possível validar todas as movimentações do extrato Wise. Confira se o PDF está completo e legível."


def _fail() -> NoReturn:
    raise PdfFinancialValidationError(_ERROR)


def _date(value: str) -> date:
    day, _, month, _, year = normalize_upper_text(value).split()
    try:
        return date(int(year), _MONTHS[month], int(day))
    except (ValueError, KeyError) as exc:
        raise PdfFinancialValidationError(_ERROR) from exc


def _money(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


@dataclass(frozen=True)
class WiseMetadata:
    currency: str
    start: date
    end: date
    closing_balance: Decimal


def extract_wise_metadata(text: str) -> WiseMetadata:
    lines = [normalize_upper_text(line) for line in text.splitlines() if line.strip()]
    titles = [line.removeprefix("EXTRATO EM ") for line in lines if line.startswith("EXTRATO EM ")]
    periods = [match for line in lines if (match := _PERIOD.fullmatch(line))]
    summaries = [match for line in lines if (match := _SUMMARY.fullmatch(line))]
    accounts = [lines[i + 1] for i, line in enumerate(lines[:-1]) if line == "NUMERO DA CONTA"]
    if len(set(titles)) != 1 or titles[0] not in WISE_CURRENCIES.values() or not periods or not summaries:
        _fail()
    if not accounts or len(set(accounts)) != 1 or not accounts[0].isdigit():
        _fail()
    bounds = {(_date(match[1]), _date(match[2])) for match in periods}
    balances = {(match[1], _date(match[2]), _money(match[3]), match[4]) for match in summaries}
    if len(bounds) != 1 or len(balances) != 1:
        _fail()
    start, end = next(iter(bounds))
    currency, balance_date, balance, trailing_currency = next(iter(balances))
    if start > end or balance_date != end or currency != titles[0] or trailing_currency != currency:
        _fail()
    return WiseMetadata(currency, start, end, balance)


@dataclass(frozen=True)
class WiseLayoutParser:
    layout_names: frozenset[str] = frozenset(WISE_CURRENCIES)

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult:
        metadata = extract_wise_metadata("\n".join(line.text for line in lines))
        if metadata.currency != WISE_CURRENCIES[layout_name]:
            _fail()
        page_numbers = sorted({line.page_number for line in lines})
        document_reference = None
        rows: list[_ParsedTransaction] = []
        financials: list[tuple[Decimal, Decimal]] = []
        references: set[str] = set()
        pending: list[_PdfLine] = []
        final_footer = False
        for page_number in page_numbers:
            page = [line for line in lines if line.page_number == page_number]
            counters = [match for line in page if (match := _PAGE.fullmatch(line.text.strip()))]
            if len(counters) != 1:
                _fail()
            counter = counters[0]
            if int(counter[2]) != page_number or int(counter[3]) != len(page_numbers):
                _fail()
            if document_reference is not None and counter[1] != document_reference:
                _fail()
            document_reference = counter[1]
            table_started = False
            footer_started = False
            for line in page:
                normalized = normalize_upper_text(line.text)
                if normalized == _TABLE:
                    table_started = True
                    continue
                if not table_started:
                    continue
                if normalized.startswith("A WISE "):
                    if pending:
                        _fail()
                    footer_started = True
                if footer_started:
                    if "WISE.COM/HELP" in normalized:
                        final_footer = page_number == page_numbers[-1]
                    continue
                transaction = _TRANSACTION.fullmatch(normalized)
                if transaction:
                    if any(_ROW.fullmatch(part.text.strip()) for part in pending[:-1]):
                        _fail()
                    match = _ROW.fullmatch(" ".join(part.text.strip() for part in pending))
                    if not match:
                        _fail()
                    transaction_date = _date(transaction[1])
                    reference = line.text.rsplit(":", 1)[-1].strip()
                    if reference in references or not metadata.start <= transaction_date <= metadata.end:
                        _fail()
                    amount, balance = _money(match[2]), _money(match[3])
                    rows.append(build_parsed_transaction(
                        date=transaction_date.isoformat(), description=match[1], amount=float(amount),
                        running_balance=float(balance), external_reference_id=reference,
                        source_page=pending[0].page_number, source_line=pending[0].line_number, has_explicit_amount_sign=True,
                        source_line_refs=tuple((part.page_number, part.line_number) for part in [*pending, line]),
                    ))
                    financials.append((amount, balance))
                    references.add(reference)
                    pending = []
                else:
                    pending.append(line)
            if not table_started:
                _fail()
        if pending or not final_footer:
            _fail()
        if rows and financials[0][1] != metadata.closing_balance:
            _fail()
        for index in range(len(rows) - 1):
            if rows[index].transaction.date < rows[index + 1].transaction.date:
                _fail()
            if financials[index][1] != financials[index + 1][1] + financials[index][0]:
                _fail()
        # Accounting consumers (including edits/exports) expect oldest first.
        # Retain each physical source page/line so the reviewer can show PDF order.
        return LayoutSpecificParseResult(list(reversed(rows)), "layout_specific_wise", "wise_description_amount_then_date_reference")
