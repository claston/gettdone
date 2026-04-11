from pathlib import Path

from app.application.csv_parser import parse_csv_transactions
from app.application.errors import InvalidFileContentError, UnsupportedFileTypeError
from app.application.models import NormalizedTransaction
from app.application.ofx_parser import parse_ofx_transactions
from app.application.xlsx_parser import parse_xlsx_transactions

_BANK_ALLOWED_EXTENSIONS = {"csv", "xlsx", "ofx"}


def parse_bank_statement_rows(filename: str, raw_bytes: bytes) -> list[NormalizedTransaction]:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in _BANK_ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError

    if extension == "csv":
        return parse_csv_transactions(raw_bytes)

    if extension == "xlsx":
        return parse_xlsx_transactions(raw_bytes)

    if extension == "ofx":
        return parse_ofx_transactions(raw_bytes)

    raise InvalidFileContentError("Unsupported bank statement content.")
