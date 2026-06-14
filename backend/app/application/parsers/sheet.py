from pathlib import Path

from app.application.errors import InvalidFileContentError, UnsupportedFileTypeError
from app.application.models import NormalizedTransaction
from app.application.normalization.amount import parse_amount
from app.application.parsers.column_mapping import resolve_sheet_field_map
from app.application.parsers.csv import parse_csv_transactions_with_mapping
from app.application.parsers.xlsx import parse_xlsx_transactions_with_mapping

_SHEET_ALLOWED_EXTENSIONS = {"csv", "xlsx"}


class ParsedOperationalSheet:
    def __init__(self, rows: list[NormalizedTransaction], mapping_detected: dict[str, str]) -> None:
        self.rows = rows
        self.mapping_detected = mapping_detected


def parse_operational_sheet_rows(filename: str, raw_bytes: bytes) -> ParsedOperationalSheet:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in _SHEET_ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError

    if extension == "csv":
        rows, field_map = parse_csv_transactions_with_mapping(
            raw_bytes=raw_bytes,
            resolver=resolve_sheet_field_map,
            required_fields={"date", "description"},
            amount_resolver=_resolve_sheet_amount_from_csv_row,
        )
        return ParsedOperationalSheet(rows=rows, mapping_detected=_required_mapping_view(field_map))

    if extension == "xlsx":
        rows, field_map = parse_xlsx_transactions_with_mapping(
            raw_bytes=raw_bytes,
            resolver=resolve_sheet_field_map,
            required_fields={"date", "description"},
            amount_resolver=_resolve_sheet_amount_from_xlsx_row,
        )
        return ParsedOperationalSheet(rows=rows, mapping_detected=_required_mapping_view(field_map))

    raise InvalidFileContentError("Unsupported operational sheet content.")


def _required_mapping_view(field_map: dict[str, str]) -> dict[str, str]:
    amount_source = field_map.get("amount", "").strip()
    if not amount_source:
        debit_source = field_map.get("debit", "").strip()
        credit_source = field_map.get("credit", "").strip()
        if debit_source or credit_source:
            amount_source = "debit/credit"

    return {
        "date": field_map.get("date", "").strip(),
        "amount": amount_source,
        "description": field_map.get("description", "").strip(),
    }


def _resolve_sheet_amount_from_csv_row(row: dict[str, str], field_map: dict[str, str]) -> float:
    amount_header = field_map.get("amount", "")
    if amount_header:
        return _parse_amount_from_raw(row.get(amount_header))

    debit_header = field_map.get("debit", "")
    credit_header = field_map.get("credit", "")
    return _resolve_split_amount(
        debit_raw=row.get(debit_header),
        credit_raw=row.get(credit_header),
    )


def _resolve_sheet_amount_from_xlsx_row(
    row_values: list[object],
    headers: list[str],
    field_map: dict[str, str],
) -> float:
    amount_header = field_map.get("amount", "")
    if amount_header:
        return _parse_amount_from_raw(_extract_xlsx_value(row_values, headers, amount_header))

    debit_header = field_map.get("debit", "")
    credit_header = field_map.get("credit", "")
    return _resolve_split_amount(
        debit_raw=_extract_xlsx_value(row_values, headers, debit_header) if debit_header else None,
        credit_raw=_extract_xlsx_value(row_values, headers, credit_header) if credit_header else None,
    )


def _resolve_split_amount(debit_raw: object | None, credit_raw: object | None) -> float:
    debit_present = _has_value(debit_raw)
    credit_present = _has_value(credit_raw)

    if debit_present and credit_present:
        raise InvalidFileContentError("Sheet row has both debit and credit values.")
    if credit_present:
        return abs(_parse_amount_from_raw(credit_raw))
    if debit_present:
        return -abs(_parse_amount_from_raw(debit_raw))

    raise InvalidFileContentError("Sheet row has empty 'amount' value.")


def _parse_amount_from_raw(value: object | None) -> float:
    if value is None:
        raise InvalidFileContentError("Sheet row has empty 'amount' value.")
    return parse_amount(str(value))


def _has_value(value: object | None) -> bool:
    return value is not None and str(value).strip() != ""


def _extract_xlsx_value(row_values: list[object], headers: list[str], mapped_header: str) -> object | None:
    try:
        index = headers.index(mapped_header)
    except ValueError as exc:
        raise InvalidFileContentError("XLSX header mapping became inconsistent.") from exc
    if index >= len(row_values):
        return None
    return row_values[index]
