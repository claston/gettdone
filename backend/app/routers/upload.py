import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, File, Form, Header, UploadFile

from app.api.conversion.conversion_error_mapper import _raise_http_convert_error
from app.api.conversion.conversion_response_mapper import _result_to_convert_response
from app.api.conversion.conversion_sse_presenter import (
    _build_conversion_upload_sse_response,
    _build_failed_upload_streaming_response,
)
from app.api.conversion.upload_staging import (
    StagedUpload as _StagedUpload,
)
from app.api.conversion.upload_staging import (
    _cleanup_staged_upload,
    _stage_upload_to_temp_file,
)
from app.application import (
    AccessControlService,
    ConvertDocumentUseCase,
    DocumentPreflightService,
    MaxPagesPerFileExceededError,
)
from app.application import conversion_service as conversion_service_module
from app.application.conversion import document_preflight_service as document_preflight_service_module
from app.application.conversion.document_preflight_service import (
    OCR_CONTEXT_SCANNED_PDF,
    OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK,
)
from app.dependencies import get_access_control_service, get_convert_document_use_case
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME
from app.schemas import ConvertResponse

router = APIRouter()
logger = logging.getLogger(__name__)
TEXT_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.TEXT_PDF_MAX_PAGES_PER_FILE
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
OCR_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.OCR_PDF_MAX_PAGES_PER_FILE
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES


def _resolve_ocr_limit_context(scanned_likely: bool | None) -> str:
    if scanned_likely is True:
        return OCR_CONTEXT_SCANNED_PDF
    return OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK


def _apply_ocr_limit_context(exc: MaxPagesPerFileExceededError, scanned_likely: bool | None) -> None:
    if not getattr(exc, "ocr_context", None):
        exc.ocr_context = _resolve_ocr_limit_context(scanned_likely)


def _resolve_conversion_type_from_filename(filename: str) -> str:
    extension = Path(filename or "").suffix.lower().strip(".")
    return f"{extension}-ofx" if extension else "pdf-ofx"


def _is_sse_request(accept: str | None) -> bool:
    return "text/event-stream" in str(accept or "").lower()


def _inspect_pdf_scan_likely(filename: str, raw_bytes: bytes) -> tuple[bool, int | None]:
    preflight = DocumentPreflightService().inspect_raw_bytes(filename=filename, raw_bytes=raw_bytes)
    return preflight.scanned_likely, preflight.estimated_pages_count


def _inspect_pdf_scan_likely_from_path(filename: str, staged_path: Path) -> tuple[bool, int | None]:
    preflight = DocumentPreflightService().inspect_staged_upload(filename=filename, staged_path=staged_path)
    return preflight.scanned_likely, preflight.estimated_pages_count


def _execute_conversion_and_build_response(
    *,
    file: UploadFile,
    staged_upload: _StagedUpload,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
    use_case: ConvertDocumentUseCase,
    on_ocr_progress=None,
    scanned_likely: bool | None = None,
    estimated_pages_count: int | None = None,
) -> ConvertResponse:
    conversion_service_module.TEXT_PDF_MAX_PAGES_PER_FILE = TEXT_PDF_MAX_PAGES_PER_FILE
    conversion_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
    conversion_service_module.OCR_PDF_MAX_PAGES_PER_FILE = OCR_PDF_MAX_PAGES_PER_FILE
    conversion_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES = OCR_PDF_MAX_UPLOAD_SIZE_BYTES
    document_preflight_service_module.TEXT_PDF_MAX_PAGES_PER_FILE = TEXT_PDF_MAX_PAGES_PER_FILE
    document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
    document_preflight_service_module.OCR_PDF_MAX_PAGES_PER_FILE = OCR_PDF_MAX_PAGES_PER_FILE
    document_preflight_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES = OCR_PDF_MAX_UPLOAD_SIZE_BYTES
    result = use_case.execute(
        filename=file.filename or "",
        staged_upload=staged_upload,
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
        on_ocr_progress=on_ocr_progress,
        scanned_likely=scanned_likely,
        estimated_pages_count=estimated_pages_count,
    )
    return _result_to_convert_response(result)



def _inspect_staged_pdf(*, file: UploadFile, staged_upload: _StagedUpload) -> tuple[bool, int | None]:
    scanned_likely, total_pages = _inspect_pdf_scan_likely_from_path(file.filename or "", staged_upload.path)
    return scanned_likely, total_pages


def _handle_conversion_upload_json(
    *,
    file: UploadFile,
    staged_upload: _StagedUpload,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
    use_case: ConvertDocumentUseCase,
    access_control_service: AccessControlService,
) -> ConvertResponse:
    identity = None
    try:
        scanned_likely, total_pages = _inspect_staged_pdf(file=file, staged_upload=staged_upload)
        return _execute_conversion_and_build_response(
            file=file,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            use_case=use_case,
            scanned_likely=scanned_likely,
            estimated_pages_count=total_pages,
        )
    except Exception as exc:
        _raise_http_convert_error(exc, identity=identity, access_control_service=access_control_service)
        raise
    finally:
        _cleanup_staged_upload(staged_upload)


@router.post("/convert", response_model=ConvertResponse)
async def convert(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    use_case: ConvertDocumentUseCase = Depends(get_convert_document_use_case),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertResponse:
    identity = None
    staged_upload: _StagedUpload | None = None
    try:
        staged_upload = await _stage_upload_to_temp_file(file)
        scanned_likely, total_pages = _inspect_pdf_scan_likely_from_path(file.filename or "", staged_upload.path)
        return _execute_conversion_and_build_response(
            file=file,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            use_case=use_case,
            scanned_likely=scanned_likely,
            estimated_pages_count=total_pages,
        )
    except Exception as exc:
        _raise_http_convert_error(exc, identity=identity, access_control_service=access_control_service)
        raise
    finally:
        _cleanup_staged_upload(staged_upload)


@router.post("/api/conversions/upload")
async def conversion_upload_stream(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    accept: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    use_case: ConvertDocumentUseCase = Depends(get_convert_document_use_case),
    access_control_service: AccessControlService = Depends(get_access_control_service),
):
    logger.info("conversion_upload_received filename=%s accept=%s", file.filename or "", accept or "")
    wants_sse = _is_sse_request(accept)
    try:
        staged_upload = await _stage_upload_to_temp_file(file)
        logger.info(
            "conversion_upload_read_complete filename=%s size_bytes=%s",
            file.filename or "",
            staged_upload.size_bytes,
        )
    except Exception as exc:
        if not wants_sse:
            _raise_http_convert_error(exc, identity=None, access_control_service=access_control_service)
            raise

        return _build_failed_upload_streaming_response(exc)

    if not wants_sse:
        return _handle_conversion_upload_json(
            file=file,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            use_case=use_case,
            access_control_service=access_control_service,
        )

    scanned_likely, total_pages = _inspect_staged_pdf(file=file, staged_upload=staged_upload)
    logger.info(
        "conversion_pdf_inspection_complete filename=%s scanned_likely=%s estimated_pages_count=%s",
        file.filename or "",
        scanned_likely,
        total_pages,
    )

    return _build_conversion_upload_sse_response(
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
        execute_conversion_and_build_response=_execute_conversion_and_build_response,
    )



