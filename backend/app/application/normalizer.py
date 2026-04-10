import re
import unicodedata
from datetime import datetime

from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d")
KNOWN_ESTABLISHMENTS = (
    ("IFOOD", ("IFOOD",)),
    ("UBER", ("UBER", "UBR")),
    ("NETFLIX", ("NETFLIX",)),
    ("SPOTIFY", ("SPOTIFY",)),
    ("MERCADO PAGO", ("MERCADO PAGO", "MERCADOPAGO")),
)
INFLOW_DESCRIPTION_KEYWORDS = ("RECEBIDO", "RECEBIMENTO", "SALARIO", "ESTORNO", "CREDITO")
OUTFLOW_DESCRIPTION_KEYWORDS = ("PAGAMENTO", "COMPRA", "DEBITO", "TARIFA", "SAQUE", "ENVIADO")


def normalize_transactions(transactions: list[NormalizedTransaction]) -> list[NormalizedTransaction]:
    return [normalize_transaction(item) for item in transactions]


def normalize_transaction(transaction: NormalizedTransaction) -> NormalizedTransaction:
    normalized_description = _normalize_description(transaction.description)
    normalized_amount = _normalize_amount(transaction.amount, transaction.type, normalized_description)
    return NormalizedTransaction(
        date=_normalize_date(transaction.date),
        description=normalized_description,
        amount=normalized_amount,
        type=_infer_type(normalized_amount, transaction.type, normalized_description),
    )


def _normalize_date(raw: str) -> str:
    value = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    if len(value) >= 10:
        try:
            return datetime.fromisoformat(value[:10]).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise InvalidFileContentError(f"Invalid date value after normalization: {raw!r}.")


def _normalize_description(raw: str) -> str:
    value = unicodedata.normalize("NFKD", str(raw).strip().upper())
    without_accents = "".join(ch for ch in value if not unicodedata.combining(ch))
    alnum_spaced = re.sub(r"[^A-Z0-9]+", " ", without_accents)
    cleaned = " ".join(alnum_spaced.split())
    return _standardize_establishment(cleaned)


def _normalize_amount(amount: float, raw_type: str, description: str) -> float:
    hint = _type_hint(raw_type) or _description_type_hint(description)
    value = float(amount)
    if hint == "inflow":
        return abs(value)
    if hint == "outflow":
        return -abs(value)
    return value


def _type_hint(raw_type: str) -> str | None:
    value = str(raw_type).strip().lower()
    if value in {"inflow", "credit", "entrada", "credito"}:
        return "inflow"
    if value in {"outflow", "debit", "saida", "debito"}:
        return "outflow"
    return None


def _description_type_hint(description: str) -> str | None:
    if any(keyword in description for keyword in INFLOW_DESCRIPTION_KEYWORDS):
        return "inflow"
    if any(keyword in description for keyword in OUTFLOW_DESCRIPTION_KEYWORDS):
        return "outflow"
    return None


def _standardize_establishment(cleaned_description: str) -> str:
    for canonical, aliases in KNOWN_ESTABLISHMENTS:
        if any(alias in cleaned_description for alias in aliases):
            return canonical
    return cleaned_description


def _infer_type(amount: float, raw_type: str, description: str) -> str:
    explicit_hint = _type_hint(raw_type) or _description_type_hint(description)
    if explicit_hint:
        return explicit_hint
    return "inflow" if amount >= 0 else "outflow"

