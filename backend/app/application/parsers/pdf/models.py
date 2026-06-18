from dataclasses import dataclass

from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.pdf_layout_inference import PdfLayoutInference


@dataclass(frozen=True)
class PdfParseResult:
    transactions: list[NormalizedTransaction]
    layout: PdfLayoutInference
    extracted_text: str
    parse_metrics: dict[str, int | float | str]
    canonical_transactions: list[CanonicalTransaction] | None = None


@dataclass(frozen=True)
class _PdfLine:
    text: str
    page_number: int
    line_number: int


@dataclass(frozen=True)
class _TabularColumnPositions:
    credit_start: int
    credit_end: int
    debit_start: int
    debit_end: int
    balance_start: int


@dataclass(frozen=True)
class _ParsedTransaction:
    transaction: NormalizedTransaction
    source_page: int | None = None
    source_line: int | None = None
    running_balance: float | None = None
    external_reference_id: str | None = None
    has_explicit_amount_sign: bool = False
