import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from time import monotonic

from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api.conversion.conversion_error_mapper import (
    CORRUPTED_PDF_USER_MESSAGE,
    _build_pages_limit_detail,
    _build_quota_exceeded_detail,
    _is_likely_corrupted_pdf_detail,
)
from app.api.conversion.upload_staging import StagedUpload, _cleanup_staged_upload
from app.application import (
    AccessControlService,
    ConvertDocumentUseCase,
    FileTooLargeError,
    InvalidFileContentError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.application.conversion import document_preflight_service as document_preflight_service_module
from app.schemas import ConvertResponse

TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_ocr_progress_percent(current_page: int, total_pages: int) -> int:
    safe_total = max(1, int(total_pages or 0))
    safe_current = max(1, min(int(current_page or 1), safe_total))
    return min(90, 78 + int((safe_current / safe_total) * 12))


def _resolve_document_progress_percent(current_page: int, total_pages: int) -> int:
    return _resolve_ocr_progress_percent(current_page, total_pages)


def _build_failed_upload_streaming_response(exc: Exception) -> StreamingResponse:
    def failed_stream(error: Exception = exc) -> Iterator[str]:
        code = "processing_failed"
        message = "Não foi possível ler este PDF."
        if isinstance(error, FileTooLargeError):
            code = "file_too_large"
            max_bytes = int(getattr(error, "_max_upload_size_bytes", TEXT_PDF_MAX_UPLOAD_SIZE_BYTES))
            max_mb = max(1, int(max_bytes // (1024 * 1024)))
            message = f"Arquivo excede o tamanho máximo de {max_mb} MB."
        yield _sse_event(
            "processing_status",
            {
                "stage": "failed",
                "progress": 5,
                "message": message,
                "code": code,
                "retryable": False,
            },
        )

    return StreamingResponse(
        failed_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@dataclass(slots=True)
class _ConversionUploadSseMachine:
    file: UploadFile
    staged_upload: StagedUpload
    anonymous_fingerprint: str | None
    user_token: str | None
    authorization: str | None
    access_cookie_token: str | None
    use_case: ConvertDocumentUseCase
    access_control_service: AccessControlService
    scanned_likely: bool
    total_pages: int | None
    execute_conversion_and_build_response: Callable[..., ConvertResponse]

    def build_response(self) -> StreamingResponse:
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        if self.scanned_likely and self.total_pages:
            headers["X-OCR-Estimated-Pages"] = str(self.total_pages)
        return StreamingResponse(
            self.iter_events(),
            media_type="text/event-stream",
            headers=headers,
            background=BackgroundTask(_cleanup_staged_upload, self.staged_upload),
        )

    def iter_events(self) -> Iterator[str]:
        yield _sse_event("processing_status", {"stage": "document_received", "progress": 5, "message": "Documento recebido."})
        yield _sse_event(
            "processing_status",
            {"stage": "document_analysis", "progress": 12, "message": "Analisando o documento..."},
        )
        yield _sse_event(
            "processing_status",
            {
                "stage": "document_preparation",
                "progress": 20,
                "message": "Preparando a leitura do documento.",
                "scannedLikely": self.scanned_likely,
            },
        )
        if self.scanned_likely:
            yield _sse_event(
                "processing_status",
                {"stage": "document_processing", "progress": 28, "message": "Processando o documento..."},
            )

        progress_queue: Queue[tuple[str, dict | ConvertResponse | Exception]] = Queue()
        thread = Thread(target=self._worker, args=(progress_queue,), daemon=True)
        thread.start()
        heartbeat_progress = 77 if self.scanned_likely else 35
        last_heartbeat_at = monotonic()

        while thread.is_alive() or not progress_queue.empty():
            try:
                kind, payload = progress_queue.get(timeout=0.2)
            except Empty:
                now = monotonic()
                if now - last_heartbeat_at >= 0.9 and thread.is_alive():
                    heartbeat_progress, heartbeat_event = self._build_heartbeat_event(heartbeat_progress)
                    yield _sse_event("processing_status", heartbeat_event)
                    last_heartbeat_at = now
                continue

            last_heartbeat_at = monotonic()
            if kind == "event":
                yield _sse_event("processing_status", payload)
                continue
            if kind == "result":
                result: ConvertResponse = payload
                yield _sse_event("processing_status", self._build_completed_event(result))
                return

            error: Exception = payload
            yield _sse_event("processing_status", self._build_failed_event(error))
            return

    def _worker(self, progress_queue: Queue[tuple[str, dict | ConvertResponse | Exception]]) -> None:
        try:
            progress_queue.put(
                (
                    "event",
                    {
                        "stage": "data_extraction",
                        "progress": 76 if self.scanned_likely else 34,
                        "message": "Extraindo informações...",
                    },
                )
            )
            payload = self.execute_conversion_and_build_response(
                file=self.file,
                staged_upload=self.staged_upload,
                anonymous_fingerprint=self.anonymous_fingerprint,
                user_token=self.user_token,
                authorization=self.authorization,
                access_cookie_token=self.access_cookie_token,
                use_case=self.use_case,
                on_ocr_progress=self._build_on_ocr_progress(progress_queue) if self.scanned_likely else None,
                scanned_likely=self.scanned_likely,
                estimated_pages_count=self.total_pages,
            )
            progress_queue.put(
                (
                    "event",
                    {
                        "stage": "preview_generation",
                        "progress": 93 if self.scanned_likely else 82,
                        "message": "Gerando prévia...",
                    },
                )
            )
            progress_queue.put(("result", payload))
        except Exception as exc:
            progress_queue.put(("error", exc))

    def _build_on_ocr_progress(
        self,
        progress_queue: Queue[tuple[str, dict | ConvertResponse | Exception]],
    ) -> Callable[[int, int], None]:
        def on_ocr_progress(current_page: int, total_page_count: int) -> None:
            safe_total = max(1, int(total_page_count or 0))
            safe_current = max(1, min(int(current_page or 1), safe_total))
            percent = _resolve_document_progress_percent(safe_current, safe_total)
            progress_queue.put(
                (
                    "event",
                    {
                        "stage": "document_processing",
                        "progress": percent,
                        "message": "Processando o documento...",
                        "currentPage": safe_current,
                        "totalPages": safe_total,
                    },
                )
            )

        return on_ocr_progress

    def _build_heartbeat_event(self, heartbeat_progress: int) -> tuple[int, dict[str, str | int]]:
        if self.scanned_likely:
            next_progress = min(97, heartbeat_progress + 1)
            return next_progress, {
                "stage": "preview_generation" if next_progress >= 88 else "data_extraction",
                "progress": next_progress,
                "message": (
                    "Finalizando o documento..."
                    if next_progress >= 94
                    else "Preparando a prévia..."
                    if next_progress >= 88
                    else "Extraindo informações..."
                ),
            }

        next_progress = min(97, heartbeat_progress + 2)
        return next_progress, {
            "stage": "preview_generation" if next_progress >= 82 else "document_processing",
            "progress": next_progress,
            "message": (
                "Finalizando o documento..."
                if next_progress >= 94
                else "Preparando a prévia..."
                if next_progress >= 82
                else "Processando o documento..."
            ),
        }

    def _build_completed_event(self, result: ConvertResponse) -> dict[str, str | int | dict]:
        return {
            "stage": "completed",
            "progress": 100,
            "message": "Conversão concluída.",
            "conversionId": result.processing_id,
            "analysisId": result.analysis.analysis_id,
            "reportUrl": f"/convert-report/{result.processing_id}",
            "convertPayload": result.model_dump(),
        }

    def _build_failed_event(self, error: Exception) -> dict[str, str | int | bool | None]:
        code = "processing_failed"
        message = "Não foi possível ler este PDF."
        retryable = False
        failed_event_payload: dict[str, str | int | bool | None] = {
            "stage": "failed",
            "progress": 90 if self.scanned_likely else 40,
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
                    OCR_PDF_MAX_UPLOAD_SIZE_BYTES if self.scanned_likely else TEXT_PDF_MAX_UPLOAD_SIZE_BYTES,
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
                message = CORRUPTED_PDF_USER_MESSAGE if _is_likely_corrupted_pdf_detail(str(error)) else str(error)
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
                quota_detail = _build_quota_exceeded_detail(
                    identity=identity,
                    access_control_service=self.access_control_service,
                )
                code = str(quota_detail["code"])
                message = str(quota_detail["message"])
                failed_event_payload.update(quota_detail)
        elif isinstance(error, MaxPagesPerFileExceededError):
            code = "pages_limit_exceeded"
            pages_limit_detail = _build_pages_limit_detail(error)
            message = str(pages_limit_detail["message"])
            failed_event_payload.update(pages_limit_detail)
        elif isinstance(error, UnsupportedFileTypeError):
            code = "unsupported_type"
            message = "Formato não suportado. Envie um PDF."
        failed_event_payload["message"] = message
        failed_event_payload["code"] = code
        failed_event_payload["retryable"] = retryable
        return failed_event_payload


def _build_conversion_upload_sse_response(
    *,
    file: UploadFile,
    staged_upload: StagedUpload,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
    use_case: ConvertDocumentUseCase,
    access_control_service: AccessControlService,
    scanned_likely: bool,
    total_pages: int | None,
    execute_conversion_and_build_response: Callable[..., ConvertResponse],
) -> StreamingResponse:
    return _ConversionUploadSseMachine(
        file=file,
        staged_upload=staged_upload,
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
        use_case=use_case,
        access_control_service=access_control_service,
        scanned_likely=scanned_likely,
        total_pages=total_pages,
        execute_conversion_and_build_response=execute_conversion_and_build_response,
    ).build_response()
