from app.application.normalization.amount import apply_amount_role_sign
from app.application.normalization.pdf_text_rules import apply_sign_hints


def compute_tabular_signed_amount(*, raw_amount: float, role: str | None, description: str, has_explicit_sign: bool = False) -> float:
    amount = apply_amount_role_sign(raw_amount, role)
    if role in {"credit", "debit"}:
        return amount
    if has_explicit_sign:
        return amount
    if amount < 0:
        return amount
    return apply_sign_hints(
        amount=amount,
        description=description,
        section_hint_value=None,
    )


def compute_hint_signed_amount(*, raw_amount: float, description: str, section_hint: str | None = None,
                               has_explicit_sign: bool = False) -> float:
    if raw_amount < 0 or has_explicit_sign:
        return raw_amount
    return apply_sign_hints(
        amount=raw_amount,
        description=description,
        section_hint_value=section_hint,
    )
