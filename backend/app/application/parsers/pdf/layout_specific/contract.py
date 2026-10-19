from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.document_extraction_models import RawDocumentExtraction
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine


@dataclass(frozen=True, slots=True)
class LayoutSpecificParseContext:
    reference_month_year: tuple[int, int] | None = None
    is_native_text: bool = False
    source_extraction: RawDocumentExtraction | None = None


@dataclass(frozen=True, slots=True)
class LayoutSpecificParseResult:
    rows: list[_ParsedTransaction]
    selected_parser: str
    selection_reason: str


class PdfLayoutParser(Protocol):
    layout_names: frozenset[str]

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None: ...
