import json
import logging
import os
import re
from hashlib import sha256
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
OCR_PDF_MAX_PAGES_PER_FILE = 10
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
OCR_CONTEXT_SCANNED_PDF = "scanned_pdf"
OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK = "unidentified_model_fallback"


def _resolve_ocr_limit_context(scanned_likely: bool | None) -> str:
    if scanned_likely is True:
        return OCR_CONTEXT_SCANNED_PDF
    return OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK


def _apply_ocr_limit_context(exc: MaxPagesPerFileExceededError, scanned_likely: bool | None) -> None:
    if not getattr(exc, "ocr_context", None):
        exc.ocr_context = _resolve_ocr_limit_context(scanned_likely)


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
CORRUPTED_PDF_USER_MESSAGE = "Parece que seu arquivo PDF está corrompido."


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_ocr_progress_percent(current_page: int, total_pages: int) -> int:
    safe_total = max(1, int(total_pages or 0))
    safe_current = max(1, min(int(current_page or 1), safe_total))
    return min(90, 78 + int((safe_current / safe_total) * 12))


def _resolve_document_progress_percent(current_page: int, total_pages: int) -> int:
    return _resolve_ocr_progress_percent(current_page, total_pages)


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


def _safe_record_anonymous_conversion_event(access_control_service: AccessControlService, **kwargs) -> None:
    try:
        access_control_service.record_anonymous_conversion_event(**kwargs)
    except Exception:
        logger.warning("Failed to persist anonymous conversion event telemetry.", exc_info=True)


def _safe_record_user_conversion(access_control_service: AccessControlService, **kwargs) -> None:
    try:
        access_control_service.record_user_conversion(**kwargs)
    except Exception:
        logger.warning("Failed to persist user conversion telemetry.", exc_info=True)


def _log_conversion_failure(
    *,
    identity,
    filename: str,
    event_id: str | None,
    error_code: str,
    error_stage: str | None,
    error_subcode: str | None,
    exception_class: str,
    scanned_likely: bool | None,
    estimated_pages_count: int | None,
    ocr_pages_processed: int,
    duration_ms: int,
    failure_diagnostics: dict[str, str | int | bool | list[str]],
) -> None:
    logger.exception(
        (
            "conversion_processing_failed event_id=%s identity_type=%s identity_id=%s filename=%s "
            "error_code=%s error_stage=%s error_subcode=%s exception_class=%s scanned_likely=%s "
            "estimated_pages_count=%s ocr_pages_processed=%s duration_ms=%s failure_diagnostics=%s"
        ),
        event_id or "",
        getattr(identity, "identity_type", "unknown"),
        getattr(identity, "identity_id", "unknown"),
        filename,
        error_code,
        error_stage or "",
        error_subcode or "",
        exception_class,
        scanned_likely,
        estimated_pages_count,
        ocr_pages_processed,
        duration_ms,
        json.dumps(failure_diagnostics, ensure_ascii=False),
    )


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
        return _resolve_ocr_max_pages_per_file(identity)
    if scanned_likely is False:
        return max(identity_max_pages, TEXT_PDF_MAX_PAGES_PER_FILE)
    return identity_max_pages


def _resolve_pdf_ocr_env_max_pages() -> int:
    raw = os.getenv("PDF_OCR_MAX_PAGES", "").strip()
    if not raw:
        return 12
    try:
        value = int(raw)
    except ValueError:
        return 12
    return max(1, value)


def _resolve_ocr_max_pages_per_file(identity) -> int:
    identity_max_pages = max(1, int(getattr(identity, "max_pages_per_file", 10**9)))
    identity_ocr_pages = max(
        1,
        int(getattr(identity, "max_pages_per_file_ocr", OCR_PDF_MAX_PAGES_PER_FILE) or OCR_PDF_MAX_PAGES_PER_FILE),
    )
    return min(identity_max_pages, identity_ocr_pages, _resolve_pdf_ocr_env_max_pages())


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
    if isinstance(metrics, dict):
        page_count = int(metrics.get("page_count", 0) or 0)
    else:
        page_count = int(getattr(metrics, "page_count", 0) or 0)
    return max(1, page_count)


def _resolve_warning_metrics(analysis) -> tuple[int, int]:
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    if metrics is None:
        return 0, 0
    if isinstance(metrics, dict):
        warning_rows = int(metrics.get("canonical_warning_transactions_count", 0) or 0)
        balance_failed = int(metrics.get("balance_consistency_failed", 0) or 0)
    else:
        warning_rows = int(getattr(metrics, "canonical_warning_transactions_count", 0) or 0)
        balance_failed = int(getattr(metrics, "balance_consistency_failed", 0) or 0)
    return max(0, warning_rows), max(0, balance_failed)


def _resolve_parse_observability_metrics(analysis) -> dict[str, str | int | float | None]:
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    if metrics is None:
        return {
            "layout_inference_name": (getattr(analysis, "layout_inference_name", None) or None),
            "layout_inference_confidence": getattr(analysis, "layout_inference_confidence", None),
            "selected_parser": None,
            "parser_selection_reason": None,
            "pdf_page_count": None,
            "extracted_char_count": None,
        }
    if isinstance(metrics, dict):
        selected_parser = metrics.get("selected_parser")
        parser_selection_reason = metrics.get("parser_selection_reason")
        page_count = metrics.get("page_count")
        extracted_char_count = metrics.get("extracted_char_count")
    else:
        selected_parser = getattr(metrics, "selected_parser", None)
        parser_selection_reason = getattr(metrics, "parser_selection_reason", None)
        page_count = getattr(metrics, "page_count", None)
        extracted_char_count = getattr(metrics, "extracted_char_count", None)
    return {
        "layout_inference_name": (getattr(analysis, "layout_inference_name", None) or None),
        "layout_inference_confidence": getattr(analysis, "layout_inference_confidence", None),
        "selected_parser": str(selected_parser).strip() if selected_parser is not None else None,
        "parser_selection_reason": str(parser_selection_reason).strip() if parser_selection_reason is not None else None,
        "pdf_page_count": int(page_count) if page_count is not None else None,
        "extracted_char_count": int(extracted_char_count) if extracted_char_count is not None else None,
    }


def _resolve_error_observability(exc: Exception) -> tuple[str | None, str | None, str]:
    exception_class = exc.__class__.__name__
    if isinstance(exc, FileTooLargeError):
        return "upload_validation", "upload_size_limit_exceeded", exception_class
    if isinstance(exc, MaxPagesPerFileExceededError):
        if getattr(exc, "ocr_context", None):
            return "ocr", "ocr_pages_limit_exceeded", exception_class
        return "upload_validation", "pdf_page_limit_exceeded", exception_class
    if isinstance(exc, InvalidUserTokenError):
        return "identity_resolution", "invalid_identity_context", exception_class
    if isinstance(exc, QuotaExceededError):
        return "quota_check", "quota_exceeded", exception_class
    if isinstance(exc, UnsupportedFileTypeError):
        return "upload_validation", "unsupported_file_type", exception_class
    if isinstance(exc, InvalidFileContentError):
        detail = str(exc).lower()
        if "password" in detail or "senha" in detail:
            return "native_pdf_read", "password_protected_pdf", exception_class
        if _is_likely_corrupted_pdf_detail(detail):
            return "native_pdf_read", "corrupted_pdf", exception_class
        if "unable to read pdf bytes" in detail:
            return "native_pdf_read", "pdf_read_failed", exception_class
        if "unsupported table layout" in detail:
            return "parse", "unsupported_table_layout", exception_class
        if "no recognizable transaction row pattern" in detail:
            return "parse", "no_transaction_row_pattern", exception_class
        if "ocr supports files up to" in detail:
            return "ocr", "ocr_file_size_limit", exception_class
        if "ocr is busy" in detail:
            return "ocr", "ocr_busy", exception_class
        if "ocr timeout" in detail:
            return "ocr", "ocr_timeout", exception_class
        if "ocr dependencies" in detail or "tesseract" in detail or "paddleocr" in detail:
            return "ocr", "ocr_dependency_missing", exception_class
        if "text" in detail or "ocr" in detail:
            return "parse", "insufficient_text", exception_class
        return "parse", "invalid_pdf_content", exception_class
    return "processing", "processing_failed", exception_class


def _build_failure_diagnostics(exc: Exception) -> dict[str, str | int | bool | list[str]]:
    detail = str(exc or "").strip()
    detail_lower = detail.lower()
    missing_signals: list[str] = []
    pdf_read_ok = "unable to read pdf bytes" not in detail_lower
    text_extracted = "text was extracted" in detail_lower or "transa" in detail_lower

    if "no recognizable transaction row pattern" in detail_lower:
        missing_signals.append("transaction_row_pattern")
    if "unsupported table layout" in detail_lower:
        missing_signals.append("supported_table_layout")
    if "text sufficient" in detail_lower or "insufficient text" in detail_lower:
        missing_signals.append("sufficient_text")
    if "ocr timeout" in detail_lower:
        missing_signals.append("ocr_completion")

    if "valor" in detail_lower or "amount" in detail_lower:
        missing_signals.append("amount_pattern")
    if "data" in detail_lower or "date" in detail_lower:
        missing_signals.append("date_pattern")

    parser_metrics: dict[str, int] = {}
    for key in ("inline_candidates", "tabular_candidates", "columnar_candidates"):
        match = re.search(rf"{key}=(\d+)", detail_lower)
        if match:
            parser_metrics[key] = int(match.group(1))
    has_date_like_match = re.search(r"has_date_like=(\d+)", detail_lower)
    has_amount_like_match = re.search(r"has_amount_like=(\d+)", detail_lower)
    detail_signals_match = re.search(r"missing_signals=([a-z_,-]+)", detail_lower)
    if detail_signals_match:
        parsed_signals = [item.strip() for item in detail_signals_match.group(1).split(",") if item.strip()]
        missing_signals.extend(parsed_signals)

    diagnostics: dict[str, str | int | bool | list[str]] = {
        "pdf_read_ok": pdf_read_ok,
        "text_extracted_likely": text_extracted,
        "missing_signals": sorted(set(missing_signals)),
        "error_detail_excerpt": detail[:240],
    }
    if has_date_like_match:
        diagnostics["has_date_like"] = bool(int(has_date_like_match.group(1)))
    if has_amount_like_match:
        diagnostics["has_amount_like"] = bool(int(has_amount_like_match.group(1)))
    if parser_metrics:
        diagnostics["inline_candidates"] = parser_metrics.get("inline_candidates", 0)
        diagnostics["tabular_candidates"] = parser_metrics.get("tabular_candidates", 0)
        diagnostics["columnar_candidates"] = parser_metrics.get("columnar_candidates", 0)
    return diagnostics


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
    file_digest = sha256(data).hexdigest() if data else None
    ocr_engine = os.getenv("PDF_OCR_ENGINE", "").strip().lower() or "tesseract"

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
                    ocr_context=OCR_CONTEXT_SCANNED_PDF if scanned_likely is True else None,
                )
        max_upload_size_bytes = _resolve_max_upload_size_bytes(identity, scanned_likely, estimated_pages_count)
        try:
            access_control_service.assert_upload_size(data, max_upload_size_bytes=max_upload_size_bytes)
        except FileTooLargeError as exc:
            setattr(exc, "_max_upload_size_bytes", max_upload_size_bytes)
            raise
        access_control_service.ensure_quota_available(identity, required_units=1)
        try:
            ocr_max_pages = _resolve_ocr_max_pages_per_file(identity)
            analysis = analyze_service.analyze(
                filename=file.filename or "",
                raw_bytes=data,
                on_ocr_progress=telemetry_ocr_progress,
                max_ocr_pages=ocr_max_pages,
            )
        except MaxPagesPerFileExceededError as exc:
            _apply_ocr_limit_context(exc, scanned_likely)
            raise
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
                        ocr_context=_resolve_ocr_limit_context(scanned_likely),
                    ) from exc
            raise
        report_service.set_convert_owner(
            analysis_id=analysis.analysis_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        pages_count = _resolve_processed_pages(analysis)
        warning_rows_count, balance_failed_count = _resolve_warning_metrics(analysis)
        parse_meta = _resolve_parse_observability_metrics(analysis)
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
                scanned_likely=scanned_likely,
                ocr_used=ocr_pages_processed > 0,
                ocr_pages_processed=ocr_pages_processed,
                duration_ms=int((monotonic() - started_at) * 1000),
                error_code=None,
                error_stage=None,
                error_subcode=None,
                exception_class=None,
                layout_inference_name=parse_meta["layout_inference_name"],
                layout_inference_confidence=parse_meta["layout_inference_confidence"],
                selected_parser=parse_meta["selected_parser"],
                parser_selection_reason=parse_meta["parser_selection_reason"],
                pdf_page_count=parse_meta["pdf_page_count"],
                extracted_char_count=parse_meta["extracted_char_count"],
                ocr_attempted=ocr_pages_processed > 0,
                ocr_engine=ocr_engine,
                file_sha256=file_digest,
                canonical_warning_transactions_count=warning_rows_count,
                balance_consistency_failed=balance_failed_count,
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
                canonical_warning_transactions_count=warning_rows_count,
                balance_consistency_failed=balance_failed_count,
                error_code=None,
                error_stage=None,
                error_subcode=None,
                exception_class=None,
                layout_inference_name=parse_meta["layout_inference_name"],
                layout_inference_confidence=parse_meta["layout_inference_confidence"],
                selected_parser=parse_meta["selected_parser"],
                parser_selection_reason=parse_meta["parser_selection_reason"],
                pdf_page_count=parse_meta["pdf_page_count"],
                extracted_char_count=parse_meta["extracted_char_count"],
                ocr_attempted=ocr_pages_processed > 0,
                ocr_engine=ocr_engine,
                file_sha256=file_digest,
            )
        return ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
    except Exception as exc:
        error_stage, error_subcode, exception_class = _resolve_error_observability(exc)
        error_code = _resolve_failed_conversion_code(exc)
        failure_diagnostics = _build_failure_diagnostics(exc)
        ocr_context = str(getattr(exc, "ocr_context", "") or "")
        if ocr_context:
            failure_diagnostics["ocr_fallback_attempted"] = True
            failure_diagnostics["ocr_fallback_reason"] = ocr_context
            if isinstance(exc, MaxPagesPerFileExceededError):
                failure_diagnostics["ocr_max_pages"] = exc.max_pages_per_file
        duration_ms = int((monotonic() - started_at) * 1000)
        ocr_attempted = ocr_pages_processed > 0 or bool(ocr_context)
        failed_event_id: str | None = None
        if identity is not None and identity.identity_type == "anonymous":
            failed_event_id = f"anon_evt_{uuid4().hex[:24]}"
            _safe_record_anonymous_conversion_event(
                access_control_service,
                event_id=failed_event_id,
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
                duration_ms=duration_ms,
                error_code=error_code,
                error_stage=error_stage,
                error_subcode=error_subcode,
                exception_class=exception_class,
                layout_inference_name=None,
                layout_inference_confidence=None,
                selected_parser=None,
                parser_selection_reason=None,
                pdf_page_count=estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=ocr_attempted,
                ocr_engine=ocr_engine,
                file_sha256=file_digest,
            )
        elif identity is not None and identity.identity_type == "user":
            _safe_record_user_conversion(
                access_control_service,
                user_id=identity.identity_id,
                processing_id=f"failed_usr_evt_{uuid4().hex[:24]}",
                filename=(file.filename or "").strip() or "unknown.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(file.filename or ""),
                status="Falha",
                transactions_count=0,
                pages_count=estimated_pages_count,
                scanned_likely=scanned_likely,
                ocr_used=ocr_pages_processed > 0,
                ocr_pages_processed=ocr_pages_processed,
                duration_ms=duration_ms,
                error_code=error_code,
                error_stage=error_stage,
                error_subcode=error_subcode,
                exception_class=exception_class,
                layout_inference_name=None,
                layout_inference_confidence=None,
                selected_parser=None,
                parser_selection_reason=None,
                pdf_page_count=estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=ocr_attempted,
                ocr_engine=ocr_engine,
                file_sha256=file_digest,
                canonical_warning_transactions_count=0,
                balance_consistency_failed=0,
                expires_at=None,
            )
        _log_conversion_failure(
            identity=identity,
            filename=(file.filename or "").strip() or "unknown.pdf",
            event_id=failed_event_id,
            error_code=error_code,
            error_stage=error_stage,
            error_subcode=error_subcode,
            exception_class=exception_class,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
            ocr_pages_processed=ocr_pages_processed,
            duration_ms=duration_ms,
            failure_diagnostics=failure_diagnostics,
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
                "scannedLikely": scanned_likely,
            },
        )
        if scanned_likely:
            yield _sse_event(
                "processing_status",
                {"stage": "document_processing", "progress": 28, "message": "Processando o documento..."},
            )

        progress_queue: Queue[tuple[str, dict | ConvertResponse | Exception]] = Queue()

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

        def worker() -> None:
            try:
                progress_queue.put(
                    ("event", {"stage": "data_extraction", "progress": 76 if scanned_likely else 34, "message": "Extraindo informações..."})
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
                            "stage": "preview_generation",
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
                        heartbeat_progress = min(97, heartbeat_progress + 1)
                        yield _sse_event(
                            "processing_status",
                            {
                                "stage": "preview_generation" if heartbeat_progress >= 88 else "data_extraction",
                                "progress": heartbeat_progress,
                                "message": (
                                    "Finalizando o documento..."
                                    if heartbeat_progress >= 94
                                    else "Preparando a prévia..."
                                    if heartbeat_progress >= 88
                                    else "Extraindo informações..."
                                ),
                            },
                        )
                    else:
                        heartbeat_progress = min(97, heartbeat_progress + 2)
                        yield _sse_event(
                            "processing_status",
                            {
                                "stage": "preview_generation" if heartbeat_progress >= 82 else "document_processing",
                                "progress": heartbeat_progress,
                                "message": (
                                    "Finalizando o documento..."
                                    if heartbeat_progress >= 94
                                    else "Preparando a prévia..."
                                    if heartbeat_progress >= 82
                                    else "Processando o documento..."
                                ),
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
            yield _sse_event("processing_status", failed_event_payload)
            return

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if scanned_likely and total_pages:
        headers["X-OCR-Estimated-Pages"] = str(total_pages)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


