from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.pdf_text_rules import apply_sign_hints


def parse_grouped_amount_line(
    *,
    raw_amount_text: str,
    description: str,
    section_hint: str | None,
) -> float:
    amount = parse_pdf_amount(raw_amount_text)
    return apply_sign_hints(
        amount=amount,
        description=description,
        section_hint_value=section_hint,
    )
