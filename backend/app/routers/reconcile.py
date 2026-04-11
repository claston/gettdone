from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.application import InvalidFileContentError, parse_operational_sheet_rows
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

    sheet_bytes = await sheet_file.read()
    try:
        parsed_sheet = parse_operational_sheet_rows(filename=sheet_filename, raw_bytes=sheet_bytes)
    except InvalidFileContentError as exc:
        detail = str(exc)
        if "missing required columns" in detail or "ambiguous column mapping" in detail:
            raise HTTPException(status_code=422, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return ReconcileIntakeResponse(
        status="accepted",
        bank_filename=bank_filename,
        bank_file_type=bank_extension,
        sheet_filename=sheet_filename,
        sheet_file_type=sheet_extension,
        sheet_rows_parsed=len(parsed_sheet.rows),
        sheet_mapping_detected=parsed_sheet.mapping_detected,
    )
