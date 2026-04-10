import csv
import unicodedata
from datetime import datetime
from io import StringIO

from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction

REQUIRED_FIELDS = {"date", "description", "amount"}

FIELD_ALIASES = {
    "date": {"date", "data", "transaction_date", "posted_at"},
    "description": {"description", "descricao", "historico", "memo"},
    "amount": {"amount", "valor", "value"},
    "type": {"type", "tipo", "operation_type", "natureza"},
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def parse_csv_transactions(raw_bytes: bytes) -> list[NormalizedTransaction]:
    text = _decode_csv_bytes(raw_bytes)
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise InvalidFileContentError("CSV does not contain headers.")

    field_map = _resolve_field_map(reader.fieldnames)
    missing = REQUIRED_FIELDS - set(field_map)
    if missing:
        raise InvalidFileContentError(f"CSV is missing required columns: {sorted(missing)}.")

    transactions: list[NormalizedTransaction] = []
    for row in reader:
        date_raw = _require_value(row, field_map["date"], "date")
        description_raw = _require_value(row, field_map["description"], "description")
        amount_raw = _require_value(row, field_map["amount"], "amount")
        type_raw = row.get(field_map["type"], "") if "type" in field_map else ""

        amount = _parse_amount(amount_raw)
        txn_type = _normalize_type(type_raw, amount)
        transactions.append(
            NormalizedTransaction(
                date=_parse_date(date_raw),
                description=description_raw.strip(),
                amount=amount,
                type=txn_type,
            )
        )

    if not transactions:
        raise InvalidFileContentError("CSV does not contain transaction rows.")
    return transactions


def _decode_csv_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InvalidFileContentError("Unable to decode CSV bytes.")


def _detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    if not sample:
        raise InvalidFileContentError("CSV is empty.")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


def _resolve_field_map(fieldnames: list[str]) -> dict[str, str]:
    normalized_lookup = {_normalize_header(header): header for header in fieldnames}
    field_map: dict[str, str] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized_lookup:
                field_map[canonical] = normalized_lookup[alias]
                break
    return field_map


def _normalize_header(header: str) -> str:
    value = unicodedata.normalize("NFKD", header.strip().lower())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _require_value(row: dict[str, str], key: str, field_name: str) -> str:
    value = row.get(key, "")
    if value is None or not str(value).strip():
        raise InvalidFileContentError(f"CSV row has empty '{field_name}' value.")
    return str(value)


def _parse_date(raw: str) -> str:
    value = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise InvalidFileContentError(f"Invalid date value: {raw!r}.")


def _parse_amount(raw: str) -> float:
    value = raw.strip().replace("R$", "").replace(" ", "")
    negative = value.startswith("(") and value.endswith(")")
    value = value.replace("(", "").replace(")", "")

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")

    try:
        parsed = float(value)
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid amount value: {raw!r}.") from exc

    if negative and parsed > 0:
        return -parsed
    return parsed


def _normalize_type(raw_type: str, amount: float) -> str:
    value = _normalize_header(raw_type)
    if value in {"inflow", "credit", "entrada", "credito"}:
        return "inflow"
    if value in {"outflow", "debit", "saida", "debito"}:
        return "outflow"
    return "inflow" if amount >= 0 else "outflow"
