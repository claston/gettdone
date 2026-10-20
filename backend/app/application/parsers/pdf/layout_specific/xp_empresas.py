"""XP digital PJ statements: descending date groups with one printed daily balance."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import NoReturn

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

XP_LAYOUT = "xp_empresas_extrato_conta_digital_pj_v1"
XP_PARSER = "layout_specific_xp_empresas"
_DATE = r"\d{2}/\d{2}/\d{4}"
_CURRENCY = r"[+\-−]?\s*R\$\s*[+\-−]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_DAY = re.compile(rf"^({_DATE})(?:\s+VALOR)?$")
_PERIOD = re.compile(rf"DE\s*({_DATE})\s+A\s+({_DATE})\b")
_MOVEMENT = re.compile(rf"^(.+?)\s+({_CURRENCY})$")
_BALANCE = re.compile(rf"^SALDO DO DIA\s+({_CURRENCY})$")
_MESSAGE = "Não foi possível conferir todas as movimentações e os saldos do extrato XP Empresas. Confira o PDF completo."


def extract_xp_account_metadata(text: str) -> tuple[str, str] | None:
    normalized = normalize_upper_text(text)
    if "EXTRATO DA CONTA DIGITAL PJ" not in normalized:
        return None
    if not (normalized.startswith("XP EMPRESAS ") or ("4003-3710" in normalized and "0800-772-0202" in normalized)):
        return None
    match = re.search(r"AGENCIA\s*(\d{4})\s*CONTA\s*(\d{4,14})(?=\s*EMITIDO EM)", normalized)
    return (match[1], match[2]) if match else None


def _fail() -> NoReturn:
    raise PdfFinancialValidationError(_MESSAGE)


def _date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _money(value: str) -> Decimal:
    compact = re.sub(r"\s+", "", value.replace("−", "-").replace("R$", ""))
    if compact.count("-") + compact.count("+") > 1:
        _fail()
    return Decimal(compact.replace(".", "").replace(",", "."))


@dataclass
class _DayGroup:
    day: date
    heading: _PdfLine
    movements: list[tuple[_PdfLine, str, Decimal]] = field(default_factory=list)
    balance: Decimal | None = None
    balance_line: _PdfLine | None = None


def _read_summary(lines: list[_PdfLine]) -> tuple[date, date, Decimal, Decimal, Decimal, Decimal]:
    titles = [i for i, line in enumerate(lines) if normalize_upper_text(line.text) == "EXTRATO DA CONTA DIGITAL PJ"]
    if len(titles) != 1:
        _fail()
    header = " ".join(normalize_upper_text(line.text) for line in lines[:titles[0]])
    amounts = []
    for label in ("SALDO INICIAL", "ENTRADAS", "SAIDAS", "SALDO FINAL"):
        matches = re.findall(rf"{label}\s*({_CURRENCY})", header)
        if len(matches) != 1:
            _fail()
        amounts.append(_money(matches[0]))
    periods = _PERIOD.findall(" ".join(normalize_upper_text(line.text) for line in lines[titles[0] + 1:]))
    if len(periods) != 1:
        _fail()
    start, end = map(_date, periods[0])
    opening, inflows, outflows, closing = amounts
    if start > end or inflows < 0 or outflows > 0 or opening + inflows + outflows != closing:
        _fail()
    return start, end, opening, inflows, outflows, closing


def _read_groups(lines: list[_PdfLine]) -> list[_DayGroup]:
    groups: list[_DayGroup] = []
    closed = False
    for line in lines:
        text = normalize_upper_text(line.text)
        if text == "FALE CONOSCO":
            closed = True
            continue
        if closed:
            if re.match(r"^\d{2}/\d{2}/", text) or "R$" in text:
                _fail()
            continue
        if heading := _DAY.fullmatch(text):
            if groups and (groups[-1].balance is None or not groups[-1].movements):
                _fail()
            group = _DayGroup(_date(heading[1]), line)
            if groups and group.day >= groups[-1].day:
                _fail()
            groups.append(group)
            continue
        if not groups:
            continue
        group = groups[-1]
        if text == "VALOR" and not group.movements:
            continue
        if balance := _BALANCE.fullmatch(text):
            if group.balance is not None or not group.movements:
                _fail()
            group.balance, group.balance_line = _money(balance[1]), line
            continue
        movement = _MOVEMENT.fullmatch(line.text.strip())
        if movement is None or group.balance is not None or "R$" in movement[1]:
            _fail()
        group.movements.append((line, movement[1].strip(), _money(movement[2])))
    if not closed or not groups or any(group.balance is None or not group.movements for group in groups):
        _fail()
    return groups


@dataclass(frozen=True, slots=True)
class XpEmpresasLayoutParser:
    layout_names: frozenset[str] = frozenset({XP_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult:
        start, end, opening, inflows, outflows, closing = _read_summary(lines)
        groups = _read_groups(lines)
        movements = [amount for group in groups for _, _, amount in group.movements]
        if sum((amount for amount in movements if amount > 0), Decimal(0)) != inflows:
            _fail()
        if sum((amount for amount in movements if amount < 0), Decimal(0)) != outflows:
            _fail()
        balance = opening
        rows: list[_ParsedTransaction] = []
        for group in reversed(groups):
            if not start <= group.day <= end:
                _fail()
            balance += sum((amount for _, _, amount in group.movements), Decimal(0))
            if balance != group.balance:
                _fail()
            for index, (line, description, amount) in enumerate(group.movements):
                is_last = index == len(group.movements) - 1
                refs = [group.heading, line]
                if is_last:
                    refs.append(group.balance_line)
                rows.append(build_parsed_transaction(
                    date=group.day.isoformat(), description=description, amount=float(amount),
                    running_balance=float(group.balance) if is_last else None,
                    source_page=line.page_number, source_line=line.line_number,
                    has_explicit_amount_sign=True,
                    source_line_refs=tuple(dict.fromkeys((part.page_number, part.line_number) for part in refs)),
                ))
        if balance != closing:
            _fail()
        return LayoutSpecificParseResult(rows, XP_PARSER, "xp_summary_totals_and_daily_balances_validated")
