from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.application import (
    InvalidFileContentError,
    parse_bank_statement_rows,
    parse_operational_sheet_rows,
)
from app.application.models import NormalizedTransaction
from app.application.normalizer import normalize_transactions
from app.schemas import ReconcileIntakeResponse

router = APIRouter()

_BANK_ALLOWED_EXTENSIONS = {"csv", "xlsx", "ofx"}
_SHEET_ALLOWED_EXTENSIONS = {"csv", "xlsx"}


def _file_extension(filename: str | None) -> str:
    if not filename:
        return ""
    return Path(filename).suffix.lower().lstrip(".")


@router.post("/reconcile", response_model=ReconcileIntakeResponse)
async def reconcile(
    bank_file: UploadFile = File(...),
    sheet_file: UploadFile = File(...),
) -> ReconcileIntakeResponse:
    bank_filename = bank_file.filename or ""
    sheet_filename = sheet_file.filename or ""
    bank_extension = _file_extension(bank_filename)
    sheet_extension = _file_extension(sheet_filename)

    if bank_extension not in _BANK_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported bank file type. Use CSV, XLSX, or OFX.")

    if sheet_extension not in _SHEET_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported sheet file type. Use CSV or XLSX.")

    bank_bytes = await bank_file.read()
    try:
        bank_rows = parse_bank_statement_rows(filename=bank_filename, raw_bytes=bank_bytes)
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sheet_bytes = await sheet_file.read()
    try:
        parsed_sheet = parse_operational_sheet_rows(filename=sheet_filename, raw_bytes=sheet_bytes)
    except InvalidFileContentError as exc:
        detail = str(exc)
        if "missing required columns" in detail or "ambiguous column mapping" in detail:
            raise HTTPException(status_code=422, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    normalized_bank_rows = normalize_transactions(_clear_type_hints(bank_rows))
    normalized_sheet_rows = normalize_transactions(_clear_type_hints(parsed_sheet.rows))

    preview: list[dict[str, str | float]] = []
    for row in normalized_bank_rows[:3]:
        preview.append(
            {
                "source": "bank",
                "date": row.date,
                "description": row.description,
                "amount": row.amount,
                "type": row.type,
            }
        )
    for row in normalized_sheet_rows[:3]:
        preview.append(
            {
                "source": "sheet",
                "date": row.date,
                "description": row.description,
                "amount": row.amount,
                "type": row.type,
            }
        )

    return ReconcileIntakeResponse(
        status="accepted",
        bank_filename=bank_filename,
        bank_file_type=bank_extension,
        sheet_filename=sheet_filename,
        sheet_file_type=sheet_extension,
        bank_rows_parsed=len(bank_rows),
        sheet_rows_parsed=len(parsed_sheet.rows),
        sheet_mapping_detected=parsed_sheet.mapping_detected,
        normalization_preview=preview,
    )


def _clear_type_hints(rows: list[NormalizedTransaction]) -> list[NormalizedTransaction]:
    return [
        NormalizedTransaction(
            date=row.date,
            description=row.description,
            amount=row.amount,
            type="",
        )
        for row in rows
    ]
