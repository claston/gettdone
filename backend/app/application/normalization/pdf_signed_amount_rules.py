from app.application.normalization.amount import apply_amount_role_sign
from app.application.normalization.pdf_text_rules import apply_sign_hints


def compute_tabular_signed_amount(*, raw_amount: float, role: str | None, description: str) -> float:
    amount = apply_amount_role_sign(raw_amount, role)
    if role in {"credit", "debit"}:
        return amount
    return apply_sign_hints(
        amount=amount,
        description=description,
        section_hint_value=None,
    )


def compute_hint_signed_amount(*, raw_amount: float, description: str, section_hint: str | None = None) -> float:
    return apply_sign_hints(
        amount=raw_amount,
        description=description,
        section_hint_value=section_hint,
    )
