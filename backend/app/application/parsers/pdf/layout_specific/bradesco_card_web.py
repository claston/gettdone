from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.date import FULL_MONTH_TO_NUMBER
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext, LayoutSpecificParseResult
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BRADESCO_CARD_WEB_LAYOUT = "bradesco_extrato_cartao_web_usd_brl_v1"
BRADESCO_CARD_WEB_PARSER = "layout_specific_bradesco_card_web"
_HEADER = re.compile(r"^DATA\s+HISTORICO\s+US\$\s+R\$$")
_MONEY = r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_VALUES = re.compile(rf"^(?P<label>.*?)\s+(?P<usd>{_MONEY})\s+(?P<brl>{_MONEY})$")
_ROW_DATE = re.compile(r"^(?P<day>\d{2})/(?P<month>\d{2})\s+(?P<rest>.+)$")
_MONTH_YEAR = rf"(?:{'|'.join(FULL_MONTH_TO_NUMBER)})/\d{{4}}"
_REFERENCE = re.compile(rf"(?P<before>{_MONTH_YEAR})\s*MES:|\bMES:\s*(?P<after>{_MONTH_YEAR})")
_MESSAGE = "Não foi possível validar as datas, os valores e os totais do extrato de cartão Bradesco. Confira o documento original."


def _reference(lines: list[_PdfLine]) -> tuple[int, int]:
    references: set[tuple[int, int]] = set()
    for line in lines:
        if _HEADER.fullmatch(normalize_upper_text(line.text)):
            break
        for match in _REFERENCE.finditer(normalize_upper_text(line.text)):
            month, year = (match["before"] or match["after"]).split("/")
            references.add((FULL_MONTH_TO_NUMBER[month], int(year)))
    if len(references) != 1:
        raise PdfFinancialValidationError(_MESSAGE)
    return references.pop()


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


@dataclass(frozen=True, slots=True)
class BradescoCardWebLayoutParser:
    layout_names: frozenset[str] = frozenset({BRADESCO_CARD_WEB_LAYOUT})

    def parse(self, *, layout_name: str, lines: list[_PdfLine], context: LayoutSpecificParseContext) -> LayoutSpecificParseResult | None:
        if layout_name not in self.layout_names:
            return None
        # This is a card movement list: neither currency column is a running balance.
        # Prefer the printed billing month over export metadata and the due month.
        reference_month, reference_year = _reference(lines)
        rows: list[_ParsedTransaction] = []
        sums = [Decimal(0), Decimal(0)]
        active = False
        validated = False
        for line in lines:
            normalized = normalize_upper_text(line.text)
            if _HEADER.fullmatch(normalized):
                active = True
                continue
            if not active or not normalized:
                continue
            values = _VALUES.fullmatch(line.text.strip())
            if values is None:
                raise PdfFinancialValidationError(_MESSAGE)
            usd, brl = _decimal(values["usd"]), _decimal(values["brl"])
            if normalize_upper_text(values["label"]) == "TOTAL:":
                if [usd, brl] != sums or not rows:
                    raise PdfFinancialValidationError(_MESSAGE)
                validated = True
                break
            row_date = _ROW_DATE.fullmatch(values["label"])
            if row_date is None:
                raise PdfFinancialValidationError(_MESSAGE)
            day, month = int(row_date["day"]), int(row_date["month"])
            year = reference_year - int(month > reference_month)
            try:
                transaction_date = date(year, month, day).isoformat()
            except ValueError as exc:
                raise PdfFinancialValidationError(_MESSAGE) from exc
            sums[0] += usd
            sums[1] += brl
            rows.append(build_parsed_transaction(
                date=transaction_date, description=row_date["rest"].strip(), amount=float(-brl),
                source_page=line.page_number, source_line=line.line_number, has_explicit_amount_sign=True,
            ))
        if not validated:
            raise PdfFinancialValidationError(_MESSAGE)
        return LayoutSpecificParseResult(
            rows=rows, selected_parser=BRADESCO_CARD_WEB_PARSER,
            selection_reason="layout_specific_bradesco_card_currency_totals_validated",
        )
