import csv
from collections.abc import Callable
from datetime import datetime
from io import StringIO

from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.normalization.amount import parse_amount
from app.application.parsers.column_mapping import FIELD_ALIASES, REQUIRED_FIELDS, normalize_header

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def parse_csv_transactions(raw_bytes: bytes) -> list[NormalizedTransaction]:
    transactions, _field_map = parse_csv_transactions_with_mapping(raw_bytes=raw_bytes)
    return transactions


def parse_csv_transactions_with_mapping(
    raw_bytes: bytes,
    resolver: Callable[[list[str]], dict[str, str]] | None = None,
    required_fields: set[str] | None = None,
    amount_resolver: Callable[[dict[str, str], dict[str, str]], float] | None = None,
) -> tuple[list[NormalizedTransaction], dict[str, str]]:
    field_resolver = resolver or _resolve_field_map
    required = required_fields or REQUIRED_FIELDS
    resolve_amount = amount_resolver or _resolve_amount_from_amount_column
    text = _decode_csv_bytes(raw_bytes)
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise InvalidFileContentError("CSV does not contain headers.")

    field_map = field_resolver(reader.fieldnames)
    missing = required - set(field_map)
    if missing:
        raise InvalidFileContentError(f"CSV is missing required columns: {sorted(missing)}.")

    transactions: list[NormalizedTransaction] = []
    for row in reader:
        date_raw = _require_value(row, field_map["date"], "date")
        description_raw = _require_value(row, field_map["description"], "description")
        type_raw = row.get(field_map["type"], "") if "type" in field_map else ""

        amount = resolve_amount(row, field_map)
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
    return transactions, field_map


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
    return normalize_header(header)


def _require_value(row: dict[str, str], key: str, field_name: str) -> str:
    value = row.get(key, "")
    if value is None or not str(value).strip():
        raise InvalidFileContentError(f"CSV row has empty '{field_name}' value.")
    return str(value)


def _resolve_amount_from_amount_column(row: dict[str, str], field_map: dict[str, str]) -> float:
    amount_raw = _require_value(row, field_map["amount"], "amount")
    return _parse_amount(amount_raw)


def _parse_date(raw: str) -> str:
    value = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise InvalidFileContentError(f"Invalid date value: {raw!r}.")


def _parse_amount(raw: str) -> float:
    return parse_amount(raw)


def _normalize_type(raw_type: str, amount: float) -> str:
    value = _normalize_header(raw_type)
    if value in {"inflow", "credit", "entrada", "credito"}:
        return "inflow"
    if value in {"outflow", "debit", "saida", "debito"}:
        return "outflow"
    return "inflow" if amount >= 0 else "outflow"
