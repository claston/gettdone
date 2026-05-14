from app.application.normalization.text import normalize_upper_text

DEBIT_TYPE_HINTS = ("DEBITO", "DEBIT", "DEB")
CREDIT_TYPE_HINTS = ("CREDITO", "CREDIT", "CRED")
COLUMNAR_HEADER_TOKENS = {
    "DATA",
    "DESCRICAO",
    "TIPO",
    "VALOR",
    "VALOR (R$)",
    "SALDO",
    "SALDO (R$)",
}


def is_transaction_type_hint(raw: str) -> bool:
    normalized = _normalize_text(raw)
    if not normalized:
        return False
    if any(token == normalized or normalized.startswith(token + " ") for token in DEBIT_TYPE_HINTS):
        return True
    if any(token == normalized or normalized.startswith(token + " ") for token in CREDIT_TYPE_HINTS):
        return True
    return False


def apply_type_sign_hint(amount: float, type_raw: str) -> float:
    normalized = _normalize_text(type_raw)
    if any(token == normalized or normalized.startswith(token + " ") for token in DEBIT_TYPE_HINTS):
        return -abs(amount)
    if any(token == normalized or normalized.startswith(token + " ") for token in CREDIT_TYPE_HINTS):
        return abs(amount)
    return amount


def is_columnar_header_line(raw: str) -> bool:
    return _normalize_text(raw) in COLUMNAR_HEADER_TOKENS


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)
