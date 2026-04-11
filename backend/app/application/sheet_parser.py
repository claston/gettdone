from pathlib import Path

from app.application.column_mapping import resolve_sheet_field_map
from app.application.csv_parser import parse_csv_transactions_with_mapping
from app.application.errors import InvalidFileContentError, UnsupportedFileTypeError
from app.application.models import NormalizedTransaction
from app.application.xlsx_parser import parse_xlsx_transactions_with_mapping

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
        rows, field_map = parse_csv_transactions_with_mapping(raw_bytes=raw_bytes, resolver=resolve_sheet_field_map)
        return ParsedOperationalSheet(rows=rows, mapping_detected=_required_mapping_view(field_map))

    if extension == "xlsx":
        rows, field_map = parse_xlsx_transactions_with_mapping(raw_bytes=raw_bytes, resolver=resolve_sheet_field_map)
        return ParsedOperationalSheet(rows=rows, mapping_detected=_required_mapping_view(field_map))

    raise InvalidFileContentError("Unsupported operational sheet content.")


def _required_mapping_view(field_map: dict[str, str]) -> dict[str, str]:
    return {
        "date": field_map.get("date", "").strip(),
        "amount": field_map.get("amount", "").strip(),
        "description": field_map.get("description", "").strip(),
    }
