"""Resolve a printed balance by its table role, independently of the bank."""
import re

from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.text import normalize_upper_text


def opening_balance_from_columns(text):
    balance_last = False
    for line in (text or "").splitlines():
        normalized = normalize_upper_text(line)
        if re.search(r"\b(?:VALOR|DEBITO|CREDITO)\b.*\bSALDO\s*(?:\(R\$\))?$", normalized):
            balance_last = True
            continue
        if balance_last and re.search(r"\bSALDO (?:ANTERIOR|INICIAL)\b", normalized):
            tokens = find_amount_tokens(line)
            if len(tokens) >= 2 and not line[tokens[-1].end:].strip():
                return parse_pdf_amount(tokens[-1].value)
    return None
