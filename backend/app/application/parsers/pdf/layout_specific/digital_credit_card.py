from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

DIGITAL_CREDIT_CARD_LAYOUT = "cartao_credito_fatura_digital_dia_mes_v1"
DIGITAL_CREDIT_CARD_PARSER = "layout_specific_digital_credit_card"
_AMOUNT = re.compile(r"(?<!\S)(?:R\$\s*)?(?P<value>[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s*$")
_CARD = re.compile(r"\bCARTAO:\s*FINAL\s+(\d{4})\b")
_MONTHS = {name: i for i, name in enumerate(("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"), 1)}


def _amount(raw: str) -> tuple[str, Decimal] | None:
    match = _AMOUNT.search(raw.replace("−", "-"))
    if match is None:
        return None
    return raw[: match.start()].strip(), Decimal(match["value"].replace(".", "").replace(",", "."))


def _unique(values: list[Decimal]) -> Decimal:
    if not values or len(set(values)) != 1:
        raise PdfFinancialValidationError()
    return values[0]


@dataclass(frozen=True, slots=True)
class InvoiceFinancials:
    opening_balance: Decimal
    closing_balance: Decimal
    payments: Decimal | None
    expenses: Decimal | None


def extract_invoice_financials(text: str) -> InvoiceFinancials:
    """Use printed global balances, never the repeating per-card subtotals."""
    openings: list[Decimal] = []
    closings: list[Decimal] = []
    payments: list[Decimal] = []
    domestic: list[Decimal] = []
    foreign: list[Decimal] = []
    summary = False
    for raw in text.splitlines():
        normalized = normalize_upper_text(raw)
        parsed = _amount(raw)
        if normalized.startswith("TOTAL DA FATURA (FINAL"):
            if parsed is None:
                raise PdfFinancialValidationError()
            summary = True
            closings.append(-parsed[1])
            continue
        if parsed is None:
            continue
        label, value = parsed
        label = normalize_upper_text(label).rstrip(":").strip()
        if label in {"SALDO ANTERIOR", "VALOR DA FATURA ANTERIOR"}:
            openings.append(-value)
        if not summary:
            continue
        if label == "(=)TOTAL DA FATURA":
            closings.append(-value)
            break
        if label == "(-)PAGAMENTOS/CREDITOS":
            payments.append(value)
        if label.startswith("(+)DESPESAS"):
            domestic.append(value)
        if label == "DESPESAS NO EXTERIOR":
            foreign.append(value)
    return InvoiceFinancials(
        opening_balance=_unique(openings),
        closing_balance=_unique(closings),
        payments=_unique(payments) if payments else None,
        expenses=(_unique(domestic) + (_unique(foreign) if foreign else Decimal(0))) if domestic else None,
    )


def _due_date(lines: list[_PdfLine]) -> date:
    in_due_header = False
    for line in lines:
        normalized = normalize_upper_text(line.text)
        if normalized.startswith("DATA LANCAMENTOS"):
            break
        if "DATA DE VENCIMENTO" in normalized:
            in_due_header = True
            inline = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", normalized)
        else:
            inline = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", normalized) if in_due_header else None
        if inline is not None:
            try:
                return date(int(inline[3]), int(inline[2]), int(inline[1]))
            except ValueError as exc:
                raise PdfFinancialValidationError() from exc
    raise PdfFinancialValidationError()


@dataclass(frozen=True, slots=True)
class DigitalCreditCardLayoutParser:
    layout_names: frozenset[str] = frozenset({DIGITAL_CREDIT_CARD_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult | None:
        if layout_name not in self.layout_names:
            return None
        # The billing year comes from the printed due date, not PDF export metadata.
        due = _due_date(lines)
        financials = extract_invoice_financials("\n".join(line.text for line in lines))
        rows: list[_ParsedTransaction] = []
        amounts: list[Decimal] = []
        current_date: date | None = None
        current_card: str | None = None
        pending: list[str] = []
        group_has_row = False
        ended = False
        index = 0

        def finish_group() -> None:
            if pending or (current_date is not None and not group_has_row):
                raise PdfFinancialValidationError()

        while index < len(lines):
            line = lines[index]
            normalized = normalize_upper_text(line.text)
            if normalized.startswith("TOTAL DA FATURA (FINAL"):
                finish_group()
                ended = True
                break
            card = _CARD.search(normalized)
            if card is not None:
                finish_group()
                current_card = card[1]
                current_date = None
                index += 1
                continue
            if normalized.startswith("DATA LANCAMENTOS"):
                finish_group()
                current_date = None
                index += 1
                continue
            if normalized.startswith("VALOR DA FATURA:"):
                index += 1
                continue
            month = normalize_upper_text(lines[index + 1].text).rstrip(".") if index + 1 < len(lines) else ""
            if re.fullmatch(r"\d{1,2}", normalized) and month in _MONTHS:
                finish_group()
                if current_card is None:
                    raise PdfFinancialValidationError()
                day, month_number = int(normalized), _MONTHS[month]
                year = due.year - int((month_number, day) > (due.month, due.day))
                try:
                    current_date = date(year, month_number, day)
                except ValueError as exc:
                    raise PdfFinancialValidationError() from exc
                group_has_row = False
                index += 2
                continue
            if current_date is not None:
                parsed = _amount(line.text)
                if parsed is None:
                    pending.append(line.text.strip())
                else:
                    description, source_amount = parsed
                    description = " ".join([*pending, description]).strip()
                    pending.clear()
                    if not description:
                        raise PdfFinancialValidationError()
                    group_has_row = True
                    if normalize_upper_text(description) != "SALDO ANTERIOR":
                        amount = -source_amount
                        amounts.append(amount)
                        rows.append(
                            build_parsed_transaction(
                                date=current_date.isoformat(),
                                description=f"{description} CARTÃO FINAL {current_card}",
                                amount=float(amount),
                                source_page=line.page_number,
                                source_line=line.line_number,
                                has_explicit_amount_sign=True,
                            )
                        )
            index += 1
        if not ended or not rows or financials.opening_balance + sum(amounts) != financials.closing_balance:
            raise PdfFinancialValidationError()
        if financials.payments is not None and sum(a for a in amounts if a > 0) != financials.payments:
            raise PdfFinancialValidationError()
        if financials.expenses is not None and -sum(a for a in amounts if a < 0) != financials.expenses:
            raise PdfFinancialValidationError()
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser=DIGITAL_CREDIT_CARD_PARSER,
            selection_reason="layout_specific_digital_invoice_totals_validated",
        )
