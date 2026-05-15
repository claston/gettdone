from app.application.normalization.pdf_amount_tokens import AmountToken, parse_pdf_amount


def parse_running_balance(balance_token: AmountToken | None) -> float | None:
    if balance_token is None:
        return None
    return parse_pdf_amount(balance_token.value)
