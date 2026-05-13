from app.application.normalization.pdf_grouped_section_rules import resolve_grouped_section_hint


def test_resolve_grouped_section_hint_updates_when_line_has_hint() -> None:
    assert resolve_grouped_section_hint("TOTAL DE ENTRADAS", current_hint=None) == "inflow"


def test_resolve_grouped_section_hint_keeps_current_when_line_has_no_hint() -> None:
    assert resolve_grouped_section_hint("COMPRA MERCADO", current_hint="outflow") == "outflow"
