from dataclasses import dataclass

import pytest

from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.registry import (
    DEFAULT_PDF_LAYOUT_PARSER_REGISTRY,
    PdfLayoutParserRegistry,
)


@dataclass
class FakeLayoutParser:
    layout_names: frozenset[str]
    calls: list[tuple[str, LayoutSpecificParseContext]]

    def parse(self, *, layout_name, lines, context):
        _ = lines
        self.calls.append((layout_name, context))
        return LayoutSpecificParseResult(
            rows=[],
            selected_parser="fake",
            selection_reason="fake_dispatch",
        )


def test_layout_parser_registry_dispatches_only_to_matching_parser() -> None:
    first = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])
    second = FakeLayoutParser(layout_names=frozenset({"layout_b"}), calls=[])
    registry = PdfLayoutParserRegistry(parsers=(first, second))
    context = LayoutSpecificParseContext(reference_month_year=(7, 2026))

    result = registry.parse(layout_name="layout_b", lines=[], context=context)

    assert result is not None
    assert result.selected_parser == "fake"
    assert first.calls == []
    assert second.calls == [("layout_b", context)]
    assert registry.parse(layout_name="unknown", lines=[], context=context) is None


def test_layout_parser_registry_rejects_duplicate_layout_registration() -> None:
    first = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])
    second = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])

    with pytest.raises(ValueError, match="layout_a"):
        PdfLayoutParserRegistry(parsers=(first, second))


def test_default_layout_parser_registry_contains_current_specific_families() -> None:
    assert DEFAULT_PDF_LAYOUT_PARSER_REGISTRY.registered_layout_names == frozenset(
        {
            "santander_cartao_credito_detalhamento_fatura_paisagem_v1",
            "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1",
            "banco_do_nordeste_extrato_consolidado_v1",
            "banco_do_nordeste_fundos_investimentos_rentabilidade_v1",
        }
    )
