"""BTG account summaries list wrapped movements newest first, with signed currency columns."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import NoReturn

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.document_period import unique_document_period
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine
from app.application.parsers.pdf.statement_review import financial_mismatch

BTG_SUMMARY_LAYOUT = "btg_pactual_extrato_conta_corrente_resumo_v1"
BTG_SUMMARY_PARSER = "layout_specific_btg_summary"
_DATE = r"\d{2}/\d{2}/\d{4}"
_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_CURRENCY = rf"[+\-−]?\s*R\$\s*{_NUMBER}"
_PERIOD = re.compile(rf"^PERIODO DE ({_DATE}) A ({_DATE})\b")
_SUMMARY_BALANCE = re.compile(rf"^SALDO DE (ABERTURA|FECHAMENTO) ({_DATE})\s+({_CURRENCY})$")
_SUMMARY_TOTAL = re.compile(rf"^TOTAL DE (ENTRADAS|SAIDAS)\s+({_CURRENCY})$")
_ROW = re.compile(rf"^({_DATE})(?:\s+(.*))?$")
_VALUES = re.compile(rf"^(.+?)\s+({_CURRENCY})\s+({_CURRENCY})$")
_ANCHOR = re.compile(rf"^SALDO DE (ABERTURA|FECHAMENTO)\s+({_CURRENCY})$")
_PAGE = re.compile(r"(?:^|\s)(\d{1,4})\s*/\s*(\d{1,4})$")
_ACCOUNT = re.compile(r"\bBANCO (\d{3})\s*[·|–-]?\s*AGENCIA (\d+(?:-\d)?)\s*[·|–-]?\s*CONTA ([\d.-]+)\b")
_CHART_TICK = re.compile(r"^R\$\s*\d{1,3}(?:\.\d{3})*$")
_TABLE = "DATA DE LANCAMENTO DESCRICAO DO LANCAMENTO ENTRADAS/SAIDAS SALDO"
_MESSAGE = "Não foi possível validar todas as movimentações e os totais do extrato BTG Pactual. Confira se o PDF está completo e legível."


def _fail() -> NoReturn:
    raise PdfFinancialValidationError(_MESSAGE)


def _date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _money(value: str) -> Decimal:
    return Decimal(re.sub(r"\s+", "", value.replace("R$", "").replace("−", "-")).replace(".", "").replace(",", "."))


@dataclass(frozen=True)
class _Summary:
    start: date
    end: date
    opening: Decimal
    closing: Decimal
    inflows: Decimal
    outflows: Decimal


def _summary(lines: list[_PdfLine]) -> _Summary:
    texts = [normalize_upper_text(line.text) for line in lines]
    periods = {(_date(match[1]), _date(match[2])) for text in texts if (match := _PERIOD.match(text))}
    balances = {
        (match[1], _date(match[2]), _money(match[3])) for text in texts if (match := _SUMMARY_BALANCE.fullmatch(text))
    }
    totals = {(match[1], _money(match[2])) for text in texts if (match := _SUMMARY_TOTAL.fullmatch(text))}
    accounts = {match.groups() for text in texts if (match := _ACCOUNT.search(text))}
    if len(balances) != 2 or len(totals) != 2 or len(accounts) != 1:
        _fail()
    if next(iter(accounts))[0] != "208":
        _fail()
    try:
        start, end = unique_document_period(periods, parse_date=lambda value: value)
    except PdfFinancialValidationError:
        _fail()
    amounts = {kind: (day, value) for kind, day, value in balances}
    total_amounts = dict(totals)
    if set(amounts) != {"ABERTURA", "FECHAMENTO"} or set(total_amounts) != {"ENTRADAS", "SAIDAS"}:
        _fail()
    opening_date, opening = amounts["ABERTURA"]
    closing_date, closing = amounts["FECHAMENTO"]
    inflows, outflows = total_amounts["ENTRADAS"], total_amounts["SAIDAS"]
    if (start is not None and opening_date != start or end is not None and closing_date != end or min(inflows, outflows) < 0):
        _fail()
    # Explicit dated opening/closing controls bound the ledger, not documentary coverage.
    start, end = start or opening_date, end or closing_date
    if opening + inflows - outflows != closing:
        financial_mismatch(_MESSAGE)
    return _Summary(start, end, opening, closing, inflows, outflows)


@dataclass(frozen=True)
class _LedgerRow:
    day: date
    description: str
    amount: Decimal
    balance: Decimal
    kind: str
    source: tuple[_PdfLine, ...]


def _ledger_row(pending: list[_PdfLine]) -> _LedgerRow | None:
    match = _ROW.fullmatch(" ".join(line.text.strip() for line in pending))
    if match is None or not match[2]:
        return None
    day = _date(match[1])
    rest = match[2]
    anchor = _ANCHOR.fullmatch(normalize_upper_text(rest))
    if anchor:
        return _LedgerRow(day, anchor[1], Decimal(0), _money(anchor[2]), anchor[1], tuple(pending))
    values = _VALUES.fullmatch(rest)
    if values is None:
        return None
    if "R$" in values[1]:
        _fail()
    return _LedgerRow(day, values[1].strip(), _money(values[2]), _money(values[3]), "movement", tuple(pending))


def _page_rows(page: list[_PdfLine], *, ordinal: int, total_pages: int) -> list[_LedgerRow]:
    headers = []
    for index, line in enumerate(page):
        if normalize_upper_text(line.text) == _TABLE:
            headers.append(index + 1)
        elif index + 1 < len(page) and normalize_upper_text(line.text + " " + page[index + 1].text) == _TABLE:
            headers.append(index + 2)
    if len(headers) != 1:
        _fail()
    rows: list[_LedgerRow] = []
    pending: list[_PdfLine] = []
    closed = False
    for line in page[headers[0]:]:
        normalized = normalize_upper_text(line.text)
        if closed:
            if _ROW.match(line.text):
                _fail()
            continue
        if normalized.startswith(("EXTRATO CONTA CORRENTE", "FALE COM NOSSA CENTRAL")) or _CHART_TICK.fullmatch(normalized):
            if pending:
                _fail()
            closed = True
            continue
        if _ROW.match(line.text):
            if pending:
                _fail()
            pending = [line]
        elif pending:
            pending.append(line)
        else:
            _fail()
        row = _ledger_row(pending)
        if row is not None:
            rows.append(row)
            pending = []
    if pending or not closed:
        _fail()
    return rows


@dataclass(frozen=True, slots=True)
class BtgSummaryLayoutParser:
    layout_names: frozenset[str] = frozenset({BTG_SUMMARY_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult:
        summary = _summary(lines)
        pages = sorted({line.page_number for line in lines})
        ledger = [row for ordinal, page in enumerate(pages, start=1)
                  for row in _page_rows([line for line in lines if line.page_number == page], ordinal=ordinal, total_pages=len(pages))]
        if ledger and ledger[0].kind == "ABERTURA" and ledger[-1].kind == "FECHAMENTO":
            ledger.reverse()
        if len(ledger) < 3 or ledger[0].kind != "FECHAMENTO" or ledger[-1].kind != "ABERTURA":
            _fail()
        if ledger[0].day != summary.end:
            _fail()
        if ledger[0].balance != summary.closing:
            financial_mismatch(_MESSAGE)
        if ledger[-1].day != summary.start:
            _fail()
        if ledger[-1].balance != summary.opening:
            financial_mismatch(_MESSAGE)
        movements = ledger[1:-1]
        if any(row.kind != "movement" or not summary.start <= row.day <= summary.end for row in movements):
            _fail()
        if sum((row.amount for row in movements if row.amount > 0), Decimal(0)) != summary.inflows:
            financial_mismatch(_MESSAGE)
        if -sum((row.amount for row in movements if row.amount < 0), Decimal(0)) != summary.outflows:
            financial_mismatch(_MESSAGE)
        for newer, older in zip(ledger, ledger[1:]):
            if newer.day < older.day:
                _fail()
            if newer.balance != older.balance + newer.amount:
                financial_mismatch(_MESSAGE)
        # Accounting consumers use oldest first; provenance retains the PDF's physical order.
        rows: list[_ParsedTransaction] = [build_parsed_transaction(
            date=row.day.isoformat(), description=row.description, amount=float(row.amount), running_balance=float(row.balance),
            source_page=row.source[0].page_number, source_line=row.source[0].line_number, has_explicit_amount_sign=True,
            source_line_refs=tuple((part.page_number, part.line_number) for part in row.source),
        ) for row in reversed(movements)]
        return LayoutSpecificParseResult(rows, BTG_SUMMARY_PARSER, "btg_summary_totals_and_all_running_balances_validated")
