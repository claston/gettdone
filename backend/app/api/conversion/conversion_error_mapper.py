import logging

from fastapi import HTTPException

from app.application import (
    AccessControlService,
    AnalysisAccessDeniedError,
    AnalysisNotFoundError,
    FileTooLargeError,
    InvalidFileContentError,
    InvalidUserTokenError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.application.conversion.document_preflight_service import (
    OCR_CONTEXT_SCANNED_PDF,
    OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK,
)

logger = logging.getLogger(__name__)
CORRUPTED_PDF_USER_MESSAGE = "Parece que seu arquivo PDF está corrompido."
TEMPORARY_BACKEND_UNAVAILABLE_CODE = "temporary_backend_unavailable"


def _build_pages_limit_user_message(exc: MaxPagesPerFileExceededError) -> str:
    context = str(getattr(exc, "ocr_context", "") or "")
    if context == OCR_CONTEXT_SCANNED_PDF:
        return (
            "Identificamos que este arquivo parece ser um documento escaneado. "
            "Ele é um pouco grande para esse tipo de processamento. "
            "Você pode dividir o PDF em arquivos menores e tentar a conversão novamente, "
            "ou enviar o arquivo para analisarmos."
        )
    if context == OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK:
        return (
            "Não identificamos automaticamente o modelo deste extrato. "
            "Tentamos ler o arquivo como um documento escaneado, mas ele é um pouco grande "
            "para esse tipo de processamento. Você pode enviar o arquivo para analisarmos "
            "o modelo ou dividir o PDF em arquivos menores e tentar a conversão novamente."
        )
    return "Este PDF é um pouco grande para o processamento. Divida o arquivo em partes menores e tente novamente."


def _build_pages_limit_detail(exc: MaxPagesPerFileExceededError) -> dict[str, str | int]:
    detail: dict[str, str | int] = {
        "code": "pages_limit_exceeded",
        "message": _build_pages_limit_user_message(exc),
        "pages_count": exc.pages_count,
        "max_pages_per_file": exc.max_pages_per_file,
    }
    context = str(getattr(exc, "ocr_context", "") or "")
    if context:
        detail["ocr_context"] = context
    return detail


def _build_quota_exceeded_detail(
    *,
    identity,
    access_control_service: AccessControlService | None = None,
) -> dict[str, str | int | None]:
    identity_type = str(getattr(identity, "identity_type", "anonymous") or "anonymous").strip().lower()
    quota_mode = str(getattr(identity, "quota_mode", "conversion") or "conversion").strip().lower()
    is_pages_mode = quota_mode == "pages"
    if identity_type == "anonymous":
        upgrade_url = "./signup.html?next=%2Fofx-convert.html&reason=quota"
    elif is_pages_mode:
        upgrade_url = None
    else:
        upgrade_url = "./planos.html?reason=quota"
    return {
        "code": "monthly_pages_quota_exceeded" if is_pages_mode else "weekly_quota_exceeded",
        "message": "Voce atingiu o limite mensal de páginas."
        if is_pages_mode
        else "Voce atingiu o limite semanal de conversões.",
        "identity_type": identity_type,
        "quota_mode": quota_mode,
        "quota_limit": int(getattr(identity, "quota_limit", 0) or 0),
        "quota_remaining": 0,
        "reset_at": access_control_service.get_quota_reset_at(identity)
        if access_control_service is not None
        and hasattr(access_control_service, "get_quota_reset_at")
        and identity is not None
        else None,
        "upgrade_url": upgrade_url,
        "support_url": "./contato.html?reason=quota",
        "plan_name": getattr(identity, "plan_name", None),
    }


def _is_likely_corrupted_pdf_detail(detail: str) -> bool:
    normalized = str(detail or "").strip().lower()
    if not normalized:
        return False
    corrupted_markers = (
        "wrong pointing object",
        "broken xref",
        "startxref",
        "eof marker",
        "malformed pdf",
        "invalid pdf",
        "corrupt",
        "corrompid",
    )
    return any(marker in normalized for marker in corrupted_markers)


def _is_temporary_backend_unavailable_error(exc: Exception) -> bool:
    detail = str(exc or "").strip().lower()
    if not detail:
        return False
    retryable_hints = (
        "failed to acquire permit to connect",
        "too many database connection attempts",
        "connection timeout",
        "network is unreachable",
        "control plane request failed",
        "timeout expired",
        "could not connect",
        "connection refused",
        "consuming input failed",
        "ssl connection has been closed unexpectedly",
        "server closed the connection unexpectedly",
        "connection is closed",
    )
    return any(token in detail for token in retryable_hints)


def _raise_http_convert_error(exc: Exception, *, identity, access_control_service: AccessControlService) -> None:
    if identity is None:
        identity = getattr(exc, "_convert_identity", None)
    if _is_temporary_backend_unavailable_error(exc):
        raise HTTPException(
            status_code=503,
            detail={
                "code": TEMPORARY_BACKEND_UNAVAILABLE_CODE,
                "message": "Servico temporariamente indisponivel. Tente novamente em instantes.",
                "retryable": True,
            },
        )
    if isinstance(exc, FileTooLargeError):
        max_bytes = int(
            getattr(
                exc,
                "_max_upload_size_bytes",
                int(identity.max_upload_size_bytes) if identity is not None else 5 * 1024 * 1024,
            )
        )
        max_mb = max(1, int(max_bytes // (1024 * 1024)))
        raise HTTPException(status_code=413, detail=f"File exceeds maximum size of {max_mb} MB.")
    if isinstance(exc, MaxPagesPerFileExceededError):
        raise HTTPException(
            status_code=400,
            detail=_build_pages_limit_detail(exc),
        )
    if isinstance(exc, InvalidUserTokenError):
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )
    if isinstance(exc, QuotaExceededError):
        raise HTTPException(
            status_code=429,
            detail=_build_quota_exceeded_detail(identity=identity, access_control_service=access_control_service),
        )
    if isinstance(exc, UnsupportedFileTypeError):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    if isinstance(exc, InvalidFileContentError):
        detail = str(exc)
        if _is_likely_corrupted_pdf_detail(detail):
            logger.warning("conversion_invalid_pdf_content_likely_corrupted detail=%s", detail)
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_pdf_content",
                    "message": CORRUPTED_PDF_USER_MESSAGE,
                },
            )
        raise HTTPException(status_code=400, detail=detail)
    if isinstance(exc, AnalysisNotFoundError):
        raise HTTPException(status_code=404, detail="Analysis not found")
    if isinstance(exc, AnalysisAccessDeniedError):
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    raise exc
