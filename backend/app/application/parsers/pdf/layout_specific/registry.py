from __future__ import annotations

from dataclasses import dataclass, field

from app.application.parsers.pdf.layout_specific.banco_do_nordeste import BancoDoNordesteLayoutParser
from app.application.parsers.pdf.layout_specific.bradesco_unificado import BradescoUnificadoLayoutParser
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
    PdfLayoutParser,
)
from app.application.parsers.pdf.layout_specific.santander_credit_card import SantanderCreditCardLayoutParser
from app.application.parsers.pdf.models import _PdfLine


@dataclass(frozen=True, slots=True)
class PdfLayoutParserRegistry:
    parsers: tuple[PdfLayoutParser, ...]
    _by_layout_name: dict[str, PdfLayoutParser] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_layout_name: dict[str, PdfLayoutParser] = {}
        for parser in self.parsers:
            for layout_name in parser.layout_names:
                if layout_name in by_layout_name:
                    raise ValueError(f"PDF layout parser already registered: {layout_name}")
                by_layout_name[layout_name] = parser
        object.__setattr__(self, "_by_layout_name", by_layout_name)

    @property
    def registered_layout_names(self) -> frozenset[str]:
        return frozenset(self._by_layout_name)

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        parser = self._by_layout_name.get(layout_name)
        if parser is None:
            return None
        return parser.parse(layout_name=layout_name, lines=lines, context=context)


DEFAULT_PDF_LAYOUT_PARSER_REGISTRY = PdfLayoutParserRegistry(
    parsers=(
        SantanderCreditCardLayoutParser(),
        BradescoUnificadoLayoutParser(),
        BancoDoNordesteLayoutParser(),
    )
)
