from __future__ import annotations

from app.application.models import NormalizedTransaction
from app.application.normalization.date import infer_default_statement_year
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine


def build_parsed_transaction(
    *,
    date: str,
    description: str,
    amount: float,
    source_page: int | None = None,
    source_line: int | None = None,
    running_balance: float | None = None,
    external_reference_id: str | None = None,
    has_explicit_amount_sign: bool = False,
) -> _ParsedTransaction:
    return _ParsedTransaction(
        transaction=NormalizedTransaction(
            date=date,
            description=description,
            amount=amount,
            type="inflow" if amount >= 0 else "outflow",
        ),
        source_page=source_page,
        source_line=source_line,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
        has_explicit_amount_sign=has_explicit_amount_sign,
    )


def infer_default_statement_year_from_lines(lines: list[_PdfLine]) -> int | None:
    return infer_default_statement_year([line.text for line in lines])


def normalize_text(value: str) -> str:
    return normalize_upper_text(value)
