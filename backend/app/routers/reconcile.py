from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.application import (
    InvalidFileContentError,
    classify_reconciliation_rows,
    generate_reconciliation_problems,
    match_exact_then_date_tolerance_then_description_similarity_1to1,
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
    ledger_match_result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=normalized_bank_rows,
        sheet_rows=normalized_sheet_rows,
    )
    classification_result = classify_reconciliation_rows(
        bank_rows=normalized_bank_rows,
        sheet_rows=normalized_sheet_rows,
        match_result=ledger_match_result,
    )

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

    exact_matches_preview: list[dict[str, str | int | float]] = []
    date_tolerance_matches_preview: list[dict[str, str | int | float]] = []
    description_similarity_matches_preview: list[dict[str, str | int | float]] = []
    for match in ledger_match_result.matches[:10]:
        match_payload = {
            "bank_index": match.bank_index,
            "sheet_index": match.sheet_index,
            "date": match.date,
            "amount": match.amount,
            "match_rule": match.match_rule,
            "reason": match.reason,
        }
        if match.match_rule == "exact":
            exact_matches_preview.append(match_payload)
        if match.match_rule == "date_tolerance":
            date_tolerance_matches_preview.append(match_payload)
        if match.match_rule == "description_similarity":
            description_similarity_matches_preview.append(match_payload)

    reconciliation_rows = [
        {
            "row_id": row.row_id,
            "source": row.source,
            "date": row.date,
            "description": row.description,
            "amount": row.amount,
            "status": row.status,
            "match_rule": row.match_rule,
            "matched_row_id": row.matched_row_id,
            "reason": row.reason,
        }
        for row in classification_result.rows
    ]
    problems = [
        {
            "type": problem.type,
            "title": problem.title,
            "description": problem.description,
        }
        for problem in generate_reconciliation_problems(classification_result.rows)
    ]

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
        exact_matches_count=ledger_match_result.exact_matches_count,
        date_tolerance_matches_count=ledger_match_result.date_tolerance_matches_count,
        description_similarity_matches_count=ledger_match_result.description_similarity_matches_count,
        total_matches_count=ledger_match_result.total_matches_count,
        conciliated_count=classification_result.conciliated_count,
        pending_count=classification_result.pending_count,
        divergent_count=classification_result.divergent_count,
        bank_unmatched_count=ledger_match_result.bank_unmatched_count,
        sheet_unmatched_count=ledger_match_result.sheet_unmatched_count,
        exact_matches_preview=exact_matches_preview,
        date_tolerance_matches_preview=date_tolerance_matches_preview,
        description_similarity_matches_preview=description_similarity_matches_preview,
        reconciliation_rows=reconciliation_rows,
        problems=problems,
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
