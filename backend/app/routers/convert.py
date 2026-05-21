import json
import logging
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pypdf import PdfReader

from app.application import (
    AccessControlService,
    AnalysisAccessDeniedError,
    AnalysisNotFoundError,
    AnalyzeService,
    FileTooLargeError,
    InvalidFileContentError,
    InvalidUserTokenError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    ReportService,
    UnsupportedFileTypeError,
)
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME, resolve_user_token_with_session
from app.schemas import ConvertResponse

router = APIRouter()
logger = logging.getLogger(__name__)
TEXT_PDF_MAX_PAGES_PER_FILE = 250
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
OCR_PDF_MAX_PAGES_PER_FILE = 15
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_ocr_progress_percent(current_page: int, total_pages: int) -> int:
    safe_total = max(1, int(total_pages or 0))
    safe_current = max(1, min(int(current_page or 1), safe_total))
    return min(90, 78 + int((safe_current / safe_total) * 12))


def _resolve_conversion_type_from_filename(filename: str) -> str:
    extension = Path(filename or "").suffix.lower().strip(".")
    return f"{extension}-ofx" if extension else "pdf-ofx"


def _resolve_failed_conversion_code(exc: Exception) -> str:
    if isinstance(exc, FileTooLargeError):
        return "file_too_large"
    if isinstance(exc, MaxPagesPerFileExceededError):
        return "pages_limit_exceeded"
    if isinstance(exc, InvalidUserTokenError):
        return "invalid_identity_context"
    if isinstance(exc, QuotaExceededError):
        return "quota_exceeded"
    if isinstance(exc, UnsupportedFileTypeError):
        return "unsupported_type"
    if isinstance(exc, InvalidFileContentError):
        detail = str(exc).lower()
        if "password" in detail or "senha" in detail:
            return "password_protected_pdf"
        if "text" in detail or "ocr" in detail:
            return "insufficient_text"
        return "invalid_pdf_content"
    return "processing_failed"


def _safe_record_anonymous_conversion_event(access_control_service: AccessControlService, **kwargs) -> None:
    try:
        access_control_service.record_anonymous_conversion_event(**kwargs)
    except Exception:
        logger.warning("Failed to persist anonymous conversion event telemetry.", exc_info=True)


def _log_pages_limit_exceeded_attempt(
    *,
    identity,
    filename: str,
    pages_count: int,
    max_pages_per_file: int,
    scanned_likely: bool | None,
) -> None:
    logger.info(
        (
            "conversion_pages_limit_exceeded identity_type=%s identity_id=%s filename=%s "
            "pages_count=%s max_pages_per_file=%s scanned_likely=%s"
        ),
        getattr(identity, "identity_type", "unknown"),
        getattr(identity, "identity_id", "unknown"),
        filename,
        pages_count,
        max_pages_per_file,
        scanned_likely,
    )


def _resolve_max_pages_per_file(identity, scanned_likely: bool | None) -> int:
    identity_max_pages = max(1, int(getattr(identity, "max_pages_per_file", 10**9)))
    if scanned_likely is True:
        return min(identity_max_pages, OCR_PDF_MAX_PAGES_PER_FILE)
    if scanned_likely is False:
        return max(identity_max_pages, TEXT_PDF_MAX_PAGES_PER_FILE)
    return identity_max_pages


def _resolve_ocr_max_pages_per_file(identity) -> int:
    identity_max_pages = max(1, int(getattr(identity, "max_pages_per_file", 10**9)))
    return min(identity_max_pages, OCR_PDF_MAX_PAGES_PER_FILE)


def _resolve_max_upload_size_bytes(
    identity,
    scanned_likely: bool | None,
    estimated_pages_count: int | None,
) -> int:
    identity_max_bytes = max(1, int(getattr(identity, "max_upload_size_bytes", 10**9)))
    if scanned_likely is True:
        return min(identity_max_bytes, OCR_PDF_MAX_UPLOAD_SIZE_BYTES)
    if scanned_likely is False and estimated_pages_count is not None:
        return max(identity_max_bytes, TEXT_PDF_MAX_UPLOAD_SIZE_BYTES)
    return identity_max_bytes


def _resolve_processed_pages(analysis) -> int | None:
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    if metrics is None:
        return None
    page_count = int(getattr(metrics, "page_count", 0) or 0)
    return max(1, page_count)


def _resolve_consumed_units(identity, analysis) -> int:
    if getattr(identity, "quota_mode", "conversion") != "pages":
        return 1
    pages_count = _resolve_processed_pages(analysis)
    return pages_count if pages_count is not None else 1


def _is_sse_request(accept: str | None) -> bool:
    return "text/event-stream" in str(accept or "").lower()


def _inspect_pdf_scan_likely(filename: str, raw_bytes: bytes) -> tuple[bool, int | None]:
    if Path(filename or "").suffix.lower() != ".pdf":
        return False, None
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        total_pages = len(reader.pages)
        extracted_chars = 0
        for page in reader.pages:
            extracted_chars += len((page.extract_text() or "").strip())
        return extracted_chars < 40, total_pages
    except Exception:
        return False, None


def _build_convert_response(
    *,
    file: UploadFile,
    data: bytes,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
    analyze_service: AnalyzeService,
    report_service: ReportService,
    access_control_service: AccessControlService,
    on_ocr_progress=None,
    scanned_likely: bool | None = None,
    estimated_pages_count: int | None = None,
) -> ConvertResponse:
    identity = None
    started_at = monotonic()
    ocr_pages_processed = 0

    def telemetry_ocr_progress(current_page: int, total_page_count: int) -> None:
        nonlocal ocr_pages_processed
        ocr_pages_processed = max(
            ocr_pages_processed,
            max(1, min(int(current_page or 1), max(1, int(total_page_count or 0)))),
        )
        if on_ocr_progress is not None:
            on_ocr_progress(current_page, total_page_count)

    try:
        resolved_user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=user_token,
            access_cookie_token=access_cookie_token,
        )
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=resolved_user_token,
        )
        if Path(file.filename or "").suffix.lower() == ".pdf" and estimated_pages_count is not None:
            # Backward compatibility: older identity fixtures/providers may not expose
            # max_pages_per_file yet. In that case, skip the limit by using a large fallback.
            max_pages_per_file = _resolve_max_pages_per_file(identity, scanned_likely)
            if int(estimated_pages_count) > max_pages_per_file:
                _log_pages_limit_exceeded_attempt(
                    identity=identity,
                    filename=file.filename or "",
                    pages_count=int(estimated_pages_count),
                    max_pages_per_file=max_pages_per_file,
                    scanned_likely=scanned_likely,
                )
                raise MaxPagesPerFileExceededError(
                    pages_count=int(estimated_pages_count),
                    max_pages_per_file=max_pages_per_file,
                )
        max_upload_size_bytes = _resolve_max_upload_size_bytes(identity, scanned_likely, estimated_pages_count)
        try:
            access_control_service.assert_upload_size(data, max_upload_size_bytes=max_upload_size_bytes)
        except FileTooLargeError as exc:
            setattr(exc, "_max_upload_size_bytes", max_upload_size_bytes)
            raise
        access_control_service.ensure_quota_available(identity, required_units=1)
        try:
            analysis = analyze_service.analyze(
                filename=file.filename or "",
                raw_bytes=data,
                on_ocr_progress=telemetry_ocr_progress,
            )
        except InvalidFileContentError as exc:
            # If inspection misclassifies a scanned PDF as text-based, analyze may fail with
            # insufficient text/OCR detail. In that case, surface a clear pages-limit error
            # when OCR caps would be exceeded.
            detail = str(exc).lower()
            if (
                Path(file.filename or "").suffix.lower() == ".pdf"
                and estimated_pages_count is not None
                and ("text" in detail or "ocr" in detail)
            ):
                ocr_max_pages = _resolve_ocr_max_pages_per_file(identity)
                if int(estimated_pages_count) > ocr_max_pages:
                    _log_pages_limit_exceeded_attempt(
                        identity=identity,
                        filename=file.filename or "",
                        pages_count=int(estimated_pages_count),
                        max_pages_per_file=ocr_max_pages,
                        scanned_likely=scanned_likely,
                    )
                    raise MaxPagesPerFileExceededError(
                        pages_count=int(estimated_pages_count),
                        max_pages_per_file=ocr_max_pages,
                    ) from exc
            raise
        report_service.set_convert_owner(
            analysis_id=analysis.analysis_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        pages_count = _resolve_processed_pages(analysis)
        consumed_units = _resolve_consumed_units(identity, analysis)
        quota_remaining = access_control_service.consume_quota(identity, consumed_units=consumed_units)
        if identity.identity_type == "user":
            file_type = str(analysis.file_type or "").strip().lower()
            conversion_type = f"{file_type}-ofx" if file_type else "pdf-ofx"
            access_control_service.record_user_conversion(
                user_id=identity.identity_id,
                processing_id=analysis.analysis_id,
                filename=(file.filename or "").strip() or f"{analysis.analysis_id}.pdf",
                model=(analysis.layout_inference_name or "").strip() or "Nao identificado",
                conversion_type=conversion_type,
                status="Sucesso",
                transactions_count=int(analysis.transactions_total),
                pages_count=pages_count,
                expires_at=analysis.expires_at,
            )
        elif identity.identity_type == "anonymous":
            _safe_record_anonymous_conversion_event(
                access_control_service,
                event_id=f"anon_evt_{uuid4().hex[:24]}",
                anonymous_fingerprint=identity.identity_id,
                filename=(file.filename or "").strip() or f"{analysis.analysis_id}.pdf",
                model=(analysis.layout_inference_name or "").strip() or "Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(file.filename or ""),
                status="Sucesso",
                transactions_count=int(analysis.transactions_total),
                pages_count=pages_count,
                scanned_likely=scanned_likely,
                ocr_used=ocr_pages_processed > 0,
                ocr_pages_processed=ocr_pages_processed,
                duration_ms=int((monotonic() - started_at) * 1000),
                error_code=None,
            )
        return ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
    except Exception as exc:
        if identity is not None and identity.identity_type == "anonymous":
            _safe_record_anonymous_conversion_event(
                access_control_service,
                event_id=f"anon_evt_{uuid4().hex[:24]}",
                anonymous_fingerprint=identity.identity_id,
                filename=(file.filename or "").strip() or "unknown.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(file.filename or ""),
                status="Falha",
                transactions_count=None,
                pages_count=estimated_pages_count,
                scanned_likely=scanned_likely,
                ocr_used=ocr_pages_processed > 0,
                ocr_pages_processed=ocr_pages_processed,
                duration_ms=int((monotonic() - started_at) * 1000),
                error_code=_resolve_failed_conversion_code(exc),
            )
        setattr(exc, "_convert_identity", identity)
        raise


def _raise_http_convert_error(exc: Exception, *, identity, access_control_service: AccessControlService) -> None:
    if identity is None:
        identity = getattr(exc, "_convert_identity", None)
    if isinstance(exc, FileTooLargeError):
        max_bytes = int(
            getattr(
                exc,
                "_max_upload_size_bytes",
                int(identity.max_upload_size_bytes) if identity is not None else 2 * 1024 * 1024,
            )
        )
        max_mb = max(1, int(max_bytes // (1024 * 1024)))
        raise HTTPException(status_code=413, detail=f"File exceeds maximum size of {max_mb} MB.")
    if isinstance(exc, MaxPagesPerFileExceededError):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "pages_limit_exceeded",
                "message": f"Este PDF tem {exc.pages_count} páginas e excede o limite de {exc.max_pages_per_file}.",
                "pages_count": exc.pages_count,
                "max_pages_per_file": exc.max_pages_per_file,
            },
        )
    if isinstance(exc, InvalidUserTokenError):
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )
    if isinstance(exc, QuotaExceededError):
        quota_limit = int(identity.quota_limit) if identity is not None else 0
        reset_at = access_control_service.get_quota_reset_at(identity) if identity is not None else None
        identity_type = str(identity.identity_type) if identity is not None else "anonymous"
        upgrade_url = "./signup.html?next=%2Fofx-convert.html&reason=quota" if identity_type == "anonymous" else None
        is_pages_mode = bool(identity is not None and str(identity.quota_mode) == "pages")
        raise HTTPException(
            status_code=429,
            detail={
                "code": "monthly_pages_quota_exceeded" if is_pages_mode else "weekly_quota_exceeded",
                "message": "Voce atingiu o limite mensal de paginas."
                if is_pages_mode
                else "Voce atingiu o limite semanal de conversoes.",
                "identity_type": identity_type,
                "quota_limit": quota_limit,
                "quota_remaining": 0,
                "reset_at": reset_at,
                "upgrade_url": upgrade_url,
            },
        )
    if isinstance(exc, UnsupportedFileTypeError):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    if isinstance(exc, InvalidFileContentError):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AnalysisNotFoundError):
        raise HTTPException(status_code=404, detail="Analysis not found")
    if isinstance(exc, AnalysisAccessDeniedError):
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    raise exc


@router.post("/convert", response_model=ConvertResponse)
async def convert(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    analyze_service: AnalyzeService = Depends(get_analyze_service),
    report_service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertResponse:
    identity = None
    try:
        data = await file.read()
        scanned_likely, total_pages = _inspect_pdf_scan_likely(file.filename or "", data)
        return _build_convert_response(
            file=file,
            data=data,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            analyze_service=analyze_service,
            report_service=report_service,
            access_control_service=access_control_service,
            scanned_likely=scanned_likely,
            estimated_pages_count=total_pages,
        )
    except Exception as exc:
        _raise_http_convert_error(exc, identity=identity, access_control_service=access_control_service)
        raise


@router.post("/api/conversions/upload")
@router.post("/conversions/upload")
async def conversion_upload_stream(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    accept: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    analyze_service: AnalyzeService = Depends(get_analyze_service),
    report_service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
):
    data = await file.read()
    if not _is_sse_request(accept):
        identity = None
        try:
            scanned_likely, total_pages = _inspect_pdf_scan_likely(file.filename or "", data)
            return _build_convert_response(
                file=file,
                data=data,
                anonymous_fingerprint=anonymous_fingerprint,
                user_token=user_token,
                authorization=authorization,
                access_cookie_token=access_cookie_token,
                analyze_service=analyze_service,
                report_service=report_service,
                access_control_service=access_control_service,
                scanned_likely=scanned_likely,
                estimated_pages_count=total_pages,
            )
        except Exception as exc:
            _raise_http_convert_error(exc, identity=identity, access_control_service=access_control_service)
            raise

    scanned_likely, total_pages = _inspect_pdf_scan_likely(file.filename or "", data)

    def event_stream():
        yield _sse_event("processing_status", {"stage": "upload_received", "progress": 5, "message": "Arquivo recebido."})
        yield _sse_event(
            "processing_status",
            {"stage": "pdf_inspection", "progress": 12, "message": "Analisando estrutura do PDF..."},
        )
        yield _sse_event(
            "processing_status",
            {
                "stage": "scan_detection",
                "progress": 20,
                "message": "Este PDF parece ser escaneado. A leitura pode levar um pouco mais."
                if scanned_likely
                else "Texto encontrado no documento.",
                "scannedLikely": scanned_likely,
            },
        )
        if scanned_likely:
            yield _sse_event(
                "processing_status",
                {"stage": "ocr_started", "progress": 28, "message": "Iniciando reconhecimento de texto..."},
            )

        progress_queue: Queue[tuple[str, dict | ConvertResponse | Exception]] = Queue()

        def on_ocr_progress(current_page: int, total_page_count: int) -> None:
            safe_total = max(1, int(total_page_count or 0))
            safe_current = max(1, min(int(current_page or 1), safe_total))
            percent = _resolve_ocr_progress_percent(safe_current, safe_total)
            progress_queue.put(
                (
                    "event",
                    {
                        "stage": "ocr_progress",
                        "progress": percent,
                        "message": f"Lendo página {safe_current} de {safe_total}...",
                        "currentPage": safe_current,
                        "totalPages": safe_total,
                    },
                )
            )

        def worker() -> None:
            try:
                progress_queue.put(
                    ("event", {"stage": "text_extraction", "progress": 76 if scanned_likely else 34, "message": "Extraindo transações..."})
                )
                payload = _build_convert_response(
                    file=file,
                    data=data,
                    anonymous_fingerprint=anonymous_fingerprint,
                    user_token=user_token,
                    authorization=authorization,
                    access_cookie_token=access_cookie_token,
                    analyze_service=analyze_service,
                    report_service=report_service,
                    access_control_service=access_control_service,
                    on_ocr_progress=on_ocr_progress if scanned_likely else None,
                    scanned_likely=scanned_likely,
                    estimated_pages_count=total_pages,
                )
                progress_queue.put(
                    (
                        "event",
                        {
                            "stage": "conversion",
                            "progress": 93 if scanned_likely else 82,
                            "message": "Gerando prévia...",
                        },
                    )
                )
                progress_queue.put(("result", payload))
            except Exception as exc:
                progress_queue.put(("error", exc))

        thread = Thread(target=worker, daemon=True)
        thread.start()
        heartbeat_progress = 77 if scanned_likely else 35
        last_heartbeat_at = monotonic()

        while thread.is_alive() or not progress_queue.empty():
            try:
                kind, payload = progress_queue.get(timeout=0.2)
            except Empty:
                now = monotonic()
                if now - last_heartbeat_at >= 0.9 and thread.is_alive():
                    if scanned_likely:
                        heartbeat_progress = min(90, heartbeat_progress + 1)
                        yield _sse_event(
                            "processing_status",
                            {
                                "stage": "text_extraction",
                                "progress": heartbeat_progress,
                                "message": "Extraindo transações...",
                            },
                        )
                    else:
                        heartbeat_progress = min(80, heartbeat_progress + 2)
                        yield _sse_event(
                            "processing_status",
                            {
                                "stage": "conversion_progress",
                                "progress": heartbeat_progress,
                                "message": "Processando arquivo...",
                            },
                        )
                    last_heartbeat_at = now
                continue
            last_heartbeat_at = monotonic()
            if kind == "event":
                yield _sse_event("processing_status", payload)
                continue
            if kind == "result":
                result: ConvertResponse = payload
                yield _sse_event(
                    "processing_status",
                    {
                        "stage": "completed",
                        "progress": 100,
                        "message": "Conversão concluída.",
                        "conversionId": result.processing_id,
                        "analysisId": result.analysis.analysis_id,
                        "reportUrl": f"/convert-report/{result.processing_id}",
                        "convertPayload": result.model_dump(),
                    },
                )
                return
            error: Exception = payload
            code = "processing_failed"
            message = "Não foi possível ler este PDF."
            retryable = False
            failed_event_payload = {
                "stage": "failed",
                "progress": 90 if scanned_likely else 40,
                "message": message,
                "code": code,
                "retryable": retryable,
            }
            if isinstance(error, FileTooLargeError):
                code = "file_too_large"
                max_bytes = int(
                    getattr(
                        error,
                        "_max_upload_size_bytes",
                        OCR_PDF_MAX_UPLOAD_SIZE_BYTES if scanned_likely else TEXT_PDF_MAX_UPLOAD_SIZE_BYTES,
                    )
                )
                max_mb = max(1, int(max_bytes // (1024 * 1024)))
                message = f"Arquivo excede o tamanho máximo de {max_mb} MB."
            elif isinstance(error, InvalidFileContentError):
                detail = str(error).lower()
                if "password" in detail or "senha" in detail:
                    code = "password_protected_pdf"
                    message = "O arquivo parece estar protegido por senha."
                elif "text" in detail or "ocr" in detail:
                    code = "insufficient_text"
                    message = "Não encontramos texto suficiente para converter este documento."
                    retryable = True
                else:
                    code = "invalid_pdf_content"
                    message = str(error)
            elif isinstance(error, QuotaExceededError):
                code = "quota_exceeded"
                message = "Você atingiu o limite do plano para conversões."
                retryable = True
                identity = getattr(error, "_convert_identity", None)
                if identity is not None:
                    identity_type = str(getattr(identity, "identity_type", "")).strip().lower()
                    if identity_type == "anonymous":
                        code = "weekly_quota_exceeded"
                        message = "Você atingiu o limite gratuito desta semana."
                    elif str(getattr(identity, "quota_mode", "")).strip().lower() == "pages":
                        code = "monthly_pages_quota_exceeded"
                        message = "Você atingiu o limite mensal de páginas do seu plano."
                    failed_event_payload["identity_type"] = identity_type
            elif isinstance(error, MaxPagesPerFileExceededError):
                code = "pages_limit_exceeded"
                message = f"Este PDF tem {error.pages_count} páginas e excede o limite de {error.max_pages_per_file}."
            elif isinstance(error, UnsupportedFileTypeError):
                code = "unsupported_type"
                message = "Formato não suportado. Envie um PDF."
            failed_event_payload["message"] = message
            failed_event_payload["code"] = code
            failed_event_payload["retryable"] = retryable
            yield _sse_event("processing_status", failed_event_payload)
            return

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if scanned_likely and total_pages:
        headers["X-OCR-Estimated-Pages"] = str(total_pages)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
