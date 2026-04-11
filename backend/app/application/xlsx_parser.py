from collections.abc import Callable
from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook

from app.application.column_mapping import REQUIRED_FIELDS, normalize_header
from app.application.csv_parser import DATE_FORMATS, _parse_amount, _resolve_field_map
from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction


def parse_xlsx_transactions(raw_bytes: bytes) -> list[NormalizedTransaction]:
    transactions, _field_map = parse_xlsx_transactions_with_mapping(raw_bytes=raw_bytes)
    return transactions


def parse_xlsx_transactions_with_mapping(
    raw_bytes: bytes,
    resolver: Callable[[list[str]], dict[str, str]] | None = None,
) -> tuple[list[NormalizedTransaction], dict[str, str]]:
    field_resolver = resolver or _resolve_field_map
    try:
        workbook = load_workbook(filename=BytesIO(raw_bytes), data_only=True, read_only=True)
    except Exception as exc:  # pragma: no cover - openpyxl exception details are not stable.
        raise InvalidFileContentError("Unable to read XLSX content.") from exc

    if not workbook.worksheets:
        raise InvalidFileContentError("XLSX does not contain worksheets.")

    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        raise InvalidFileContentError("XLSX does not contain headers.")

    fieldnames = [str(item).strip() if item is not None else "" for item in header_row]
    if not any(fieldnames):
        raise InvalidFileContentError("XLSX does not contain headers.")

    field_map = field_resolver(fieldnames)
    missing = REQUIRED_FIELDS - set(field_map)
    if missing:
        raise InvalidFileContentError(f"XLSX is missing required columns: {sorted(missing)}.")

    transactions: list[NormalizedTransaction] = []
    for raw_row in rows_iter:
        if not raw_row:
            continue
        row_values = list(raw_row)
        if all(item is None or str(item).strip() == "" for item in row_values):
            continue

        date_raw = _require_value(row_values, fieldnames, field_map["date"], "date")
        description_raw = _require_value(row_values, fieldnames, field_map["description"], "description")
        amount_raw = _require_value(row_values, fieldnames, field_map["amount"], "amount")
        type_raw = _get_optional_value(row_values, fieldnames, field_map.get("type"))

        amount = _parse_amount(str(amount_raw))
        transactions.append(
            NormalizedTransaction(
                date=_parse_date(date_raw),
                description=str(description_raw).strip(),
                amount=amount,
                type=_normalize_type(type_raw, amount),
            )
        )

    if not transactions:
        raise InvalidFileContentError("XLSX does not contain transaction rows.")
    return transactions, field_map


def _require_value(row_values: list[object], headers: list[str], mapped_header: str, field_name: str) -> object:
    value = _extract_value(row_values, headers, mapped_header)
    if value is None or str(value).strip() == "":
        raise InvalidFileContentError(f"XLSX row has empty '{field_name}' value.")
    return value


def _get_optional_value(row_values: list[object], headers: list[str], mapped_header: str | None) -> str:
    if not mapped_header:
        return ""
    value = _extract_value(row_values, headers, mapped_header)
    return "" if value is None else str(value)


def _extract_value(row_values: list[object], headers: list[str], mapped_header: str) -> object | None:
    try:
        index = headers.index(mapped_header)
    except ValueError as exc:
        raise InvalidFileContentError("XLSX header mapping became inconsistent.") from exc
    if index >= len(row_values):
        return None
    return row_values[index]


def _parse_date(raw: object) -> str:
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")

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
    raise InvalidFileContentError(f"Invalid date value: {raw!r}.")


def _normalize_type(raw_type: str, amount: float) -> str:
    value = normalize_header(raw_type)
    if value in {"inflow", "credit", "entrada", "credito"}:
        return "inflow"
    if value in {"outflow", "debit", "saida", "debito"}:
        return "outflow"
    return "inflow" if amount >= 0 else "outflow"
