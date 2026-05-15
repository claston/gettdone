from app.application.normalization.pdf_amount_tokens import is_amount_like
from app.application.normalization.pdf_columnar_rules import is_columnar_header_line, is_transaction_type_hint


def is_valid_columnar_transaction_row(*, description: str, type_raw: str, amount_raw: str) -> bool:
    if not description or is_columnar_header_line(description):
        return False
    if not is_transaction_type_hint(type_raw):
        return False
    if not is_amount_like(amount_raw):
        return False
    return True
