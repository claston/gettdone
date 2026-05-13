from app.application.normalization.pdf_signed_amount_rules import compute_hint_signed_amount, compute_tabular_signed_amount


def test_compute_tabular_signed_amount_uses_role_when_credit_debit() -> None:
    assert compute_tabular_signed_amount(raw_amount=10.0, role="debit", description="PIX") == -10.0
    assert compute_tabular_signed_amount(raw_amount=10.0, role="credit", description="PIX") == 10.0


def test_compute_tabular_signed_amount_uses_description_hint_without_role() -> None:
    assert compute_tabular_signed_amount(raw_amount=10.0, role=None, description="ESTORNO TARIFA") == 10.0


def test_compute_hint_signed_amount_respects_section_hint() -> None:
    assert compute_hint_signed_amount(raw_amount=10.0, description="TRANSFERENCIA", section_hint="outflow") == -10.0
