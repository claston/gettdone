import json
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic

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
    QuotaExceededError,
    ReportService,
    UnsupportedFileTypeError,
)
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME, resolve_user_token_with_session
from app.schemas import ConvertResponse

router = APIRouter()


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_ocr_progress_percent(current_page: int, total_pages: int) -> int:
    safe_total = max(1, int(total_pages or 0))
    safe_current = max(1, min(int(current_page or 1), safe_total))
    return min(90, 78 + int((safe_current / safe_total) * 12))


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
) -> ConvertResponse:
    identity = None
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
        access_control_service.assert_upload_size(data, max_upload_size_bytes=identity.max_upload_size_bytes)
        access_control_service.ensure_quota_available(identity, required_units=1)
        if on_ocr_progress is None:
            analysis = analyze_service.analyze(filename=file.filename or "", raw_bytes=data)
        else:
            analysis = analyze_service.analyze(
                filename=file.filename or "", raw_bytes=data, on_ocr_progress=on_ocr_progress
            )
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
        return ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
    except Exception as exc:
        setattr(exc, "_convert_identity", identity)
        raise


def _raise_http_convert_error(exc: Exception, *, identity, access_control_service: AccessControlService) -> None:
    if identity is None:
        identity = getattr(exc, "_convert_identity", None)
    if isinstance(exc, FileTooLargeError):
        max_bytes = int(identity.max_upload_size_bytes) if identity is not None else 2 * 1024 * 1024
        max_mb = max(1, int(max_bytes // (1024 * 1024)))
        raise HTTPException(status_code=413, detail=f"File exceeds maximum size of {max_mb} MB.")
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
        return await convert(
            file=file,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            analyze_service=analyze_service,
            report_service=report_service,
            access_control_service=access_control_service,
        )

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
                )
                progress_queue.put(("event", {"stage": "conversion", "progress": 93 if scanned_likely else 82, "message": "Gerando prévia..."}))
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
            if isinstance(error, InvalidFileContentError):
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
            elif isinstance(error, UnsupportedFileTypeError):
                code = "unsupported_type"
                message = "Formato não suportado. Envie um PDF."
            yield _sse_event(
                "processing_status",
                {
                    "stage": "failed",
                    "progress": 90 if scanned_likely else 40,
                    "message": message,
                    "code": code,
                    "retryable": retryable,
                },
            )
            return

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if scanned_likely and total_pages:
        headers["X-OCR-Estimated-Pages"] = str(total_pages)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
