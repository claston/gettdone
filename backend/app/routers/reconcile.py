import asyncio
from functools import partial
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile

from app.application import (
    AccessControlService,
    ConversionCapacityController,
    InvalidFileContentError,
    InvalidUserTokenError,
    QuotaExceededError,
    ReportService,
    classify_reconciliation_rows,
    generate_reconciliation_problems,
    match_exact_then_date_tolerance_then_description_similarity_1to1,
    parse_bank_statement_rows,
    parse_operational_sheet_rows,
)
from app.application.conversion.document_preflight_service import TEXT_PDF_MAX_PAGES_PER_FILE, DocumentPreflightService
from app.application.document_classifier import classify_document
from app.application.errors import FileTooLargeError, MaxPagesPerFileExceededError
from app.application.models import NormalizedTransaction
from app.application.normalization.transaction_normalizer import normalize_transactions
from app.application.parsers.pdf.reader import open_pdf_reader
from app.dependencies import get_access_control_service, get_conversion_capacity_controller, get_report_service
from app.routers.access_control_common import set_anonymous_identity_cookie
from app.routers.auth_session import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    resolve_anonymous_fingerprint_with_cookie,
    resolve_user_token_with_session,
)
from app.routers.public_request_security import enforce_public_request
from app.schemas import ReconcileIntakeResponse

router = APIRouter()

_BANK_ALLOWED_EXTENSIONS = {"csv", "xlsx", "ofx", "pdf"}
_SHEET_ALLOWED_EXTENSIONS = {"csv", "xlsx"}
_SHEET_BANK_STATEMENT_WARN_THRESHOLD = 0.55
RECONCILE_MAX_FILE_BYTES = 10 * 1024 * 1024


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    content = bytearray()
    while chunk := await file.read(min(1024 * 1024, limit + 1 - len(content))):
        content.extend(chunk)
        if len(content) > limit:
            raise HTTPException(413, "O arquivo excede o tamanho máximo permitido.")
    return bytes(content)


def _file_extension(filename: str | None) -> str:
    if not filename:
        return ""
    return Path(filename).suffix.lower().lstrip(".")


@router.post("/reconcile", response_model=ReconcileIntakeResponse)
async def reconcile(
    request: Request,
    response: Response,
    bank_file: UploadFile = File(...),
    sheet_file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    report_service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    capacity: ConversionCapacityController = Depends(get_conversion_capacity_controller),
) -> ReconcileIntakeResponse:
    try:
        token = resolve_user_token_with_session(
            access_control_service=access_control_service, authorization=authorization,
            explicit_user_token=user_token, access_cookie_token=access_cookie_token)
        fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service, anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=anonymous_fingerprint)
        if not token and not fingerprint:
            fingerprint = access_control_service.generate_anonymous_fingerprint()
            set_anonymous_identity_cookie(response, token=access_control_service.issue_anonymous_identity_token(fingerprint=fingerprint))
        identity = access_control_service.resolve_identity(anonymous_fingerprint=fingerprint, user_token=token)
        access_control_service.ensure_quota_available(identity)
    except InvalidUserTokenError:
        raise HTTPException(401, "Identidade inválida. Inicie uma sessão ou entre novamente.") from None
    except QuotaExceededError:
        raise HTTPException(429, "Limite de processamento atingido.") from None
    lease = capacity.try_acquire(source="/reconcile")
    if lease is None:
        raise HTTPException(503, "Aguarde a conclusão dos arquivos em processamento.",
                            headers={"Retry-After": str(capacity.retry_after_seconds)})
    submitted = False
    try:
        enforce_public_request(access_control_service, request, kind="conversion", identity=identity)
        limit = min(RECONCILE_MAX_FILE_BYTES, identity.max_upload_size_bytes)
        # PDF type is determined by preflight after a bounded read. Native PDFs
        # may use the shared 10 MiB allowance even for a 5 MiB identity.
        bank_limit = RECONCILE_MAX_FILE_BYTES if _file_extension(bank_file.filename) == "pdf" else limit
        bank_bytes = await _read_bounded(bank_file, bank_limit)
        sheet_bytes = await _read_bounded(sheet_file, limit)
        future = capacity.submit(lease, partial(
            _process_reconcile, bank_filename=bank_file.filename or "", sheet_filename=sheet_file.filename or "",
            bank_bytes=bank_bytes, sheet_bytes=sheet_bytes, identity=identity,
            report_service=report_service, access_control_service=access_control_service))
        submitted = True
        return await asyncio.wrap_future(future)
    except QuotaExceededError:
        raise HTTPException(429, "Limite de processamento atingido.") from None
    finally:
        if not submitted:
            lease.release(outcome="reconcile_rejected")


def _process_reconcile(*, bank_filename, sheet_filename, bank_bytes, sheet_bytes, identity,
                       report_service, access_control_service):
    bank_extension = _file_extension(bank_filename)
    sheet_extension = _file_extension(sheet_filename)

    if bank_extension not in _BANK_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported bank file type. Use CSV, XLSX, OFX, or PDF.")

    if sheet_extension not in _SHEET_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported sheet file type. Use CSV or XLSX.")

    required_units = 1
    if bank_extension == "pdf":
        try:
            pages = len(open_pdf_reader(BytesIO(bank_bytes)).pages)
            if pages > TEXT_PDF_MAX_PAGES_PER_FILE:
                raise HTTPException(413, "O PDF excede o limite de páginas permitido.")
            preflight = DocumentPreflightService()
            inspected = preflight.inspect_raw_bytes(filename=bank_filename, raw_bytes=bank_bytes)
            preflight.build_policy(identity=identity, filename=bank_filename,
                                   staged_upload_size_bytes=len(bank_bytes), preflight_result=inspected)
        except (FileTooLargeError, MaxPagesPerFileExceededError):
            raise HTTPException(413, "O PDF excede os limites de tamanho ou páginas permitidos.") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Não foi possível ler o PDF enviado.") from None
        if identity.quota_mode == "pages":
            required_units = max(1, pages)
    access_control_service.ensure_quota_available(identity, required_units=required_units)

    bank_classification = classify_document(filename=bank_filename, raw_bytes=bank_bytes)
    try:
        bank_rows = parse_bank_statement_rows(filename=bank_filename, raw_bytes=bank_bytes)
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sheet_classification = classify_document(filename=sheet_filename, raw_bytes=sheet_bytes)
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
    if (
        sheet_classification.semantic_type == "extrato_bancario"
        and sheet_classification.confidence >= _SHEET_BANK_STATEMENT_WARN_THRESHOLD
    ):
        problems.insert(
            0,
            {
                "type": "sheet_looks_like_bank_statement",
                "title": "Planilha parece extrato bancario",
                "description": (
                    "O arquivo enviado no campo de planilha parece um extrato bancario "
                    "e deve ser revisado antes da conciliacao."
                ),
            },
        )
    summary = {
        "total_bank_rows": len(normalized_bank_rows),
        "total_sheet_rows": len(normalized_sheet_rows),
        "conciliated_count": classification_result.conciliated_count,
        "pending_count": classification_result.pending_count,
        "divergent_count": classification_result.divergent_count,
    }
    access_control_service.consume_quota(identity, consumed_units=required_units)
    analysis_id, expires_at = report_service.save_reconcile_report(
        summary=summary,
        reconciliation_rows=reconciliation_rows,
        problems=problems,
    )
    report_service.set_reconcile_owner(
        analysis_id=analysis_id, identity_type=identity.identity_type, identity_id=identity.identity_id)

    return ReconcileIntakeResponse(
        analysis_id=analysis_id,
        status="accepted",
        bank_filename=bank_filename,
        bank_file_type=bank_extension,
        bank_semantic_type=bank_classification.semantic_type,
        bank_semantic_confidence=bank_classification.confidence,
        bank_semantic_evidence=bank_classification.evidence,
        sheet_filename=sheet_filename,
        sheet_file_type=sheet_extension,
        sheet_semantic_type=sheet_classification.semantic_type,
        sheet_semantic_confidence=sheet_classification.confidence,
        sheet_semantic_evidence=sheet_classification.evidence,
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
        summary=summary,
        expires_at=expires_at,
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
