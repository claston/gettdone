from app.application.normalization.pdf_grouped_amount_line_rules import parse_grouped_amount_line


def test_parse_grouped_amount_line_applies_outflow_hint() -> None:
    assert parse_grouped_amount_line(raw_amount_text="10,00", description="PAGAMENTO PIX", section_hint=None) == -10.0


def test_parse_grouped_amount_line_applies_section_hint_when_description_is_neutral() -> None:
    assert parse_grouped_amount_line(raw_amount_text="10,00", description="TRANSFERENCIA", section_hint="inflow") == 10.0
