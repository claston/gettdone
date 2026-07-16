import json
import logging
import os
import re

from app.api.conversion.conversion_error_mapper import _is_likely_corrupted_pdf_detail
from app.application import (
    AccessControlService,
    FileTooLargeError,
    InvalidFileContentError,
    InvalidUserTokenError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.application.conversion import document_preflight_service as document_preflight_service_module

logger = logging.getLogger(__name__)
TEXT_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.TEXT_PDF_MAX_PAGES_PER_FILE
OCR_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.OCR_PDF_MAX_PAGES_PER_FILE
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES


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
            "extraction_provider": None,
            "textract_used": 0,
            "textract_attempted": 0,
            "textract_error_type": None,
            "native_text_detected": 0,
        }
    if isinstance(metrics, dict):
        selected_parser = metrics.get("selected_parser")
        parser_selection_reason = metrics.get("parser_selection_reason")
        page_count = metrics.get("page_count")
        extracted_char_count = metrics.get("extracted_char_count")
        extraction_provider = metrics.get("extraction_provider")
        textract_used = metrics.get("textract_used")
        textract_attempted = metrics.get("textract_attempted")
        textract_error_type = metrics.get("textract_error_type")
        native_text_detected = metrics.get("native_text_detected")
    else:
        selected_parser = getattr(metrics, "selected_parser", None)
        parser_selection_reason = getattr(metrics, "parser_selection_reason", None)
        page_count = getattr(metrics, "page_count", None)
        extracted_char_count = getattr(metrics, "extracted_char_count", None)
        extraction_provider = getattr(metrics, "extraction_provider", None)
        textract_used = getattr(metrics, "textract_used", None)
        textract_attempted = getattr(metrics, "textract_attempted", None)
        textract_error_type = getattr(metrics, "textract_error_type", None)
        native_text_detected = getattr(metrics, "native_text_detected", None)
    return {
        "layout_inference_name": (getattr(analysis, "layout_inference_name", None) or None),
        "layout_inference_confidence": getattr(analysis, "layout_inference_confidence", None),
        "selected_parser": str(selected_parser).strip() if selected_parser is not None else None,
        "parser_selection_reason": str(parser_selection_reason).strip() if parser_selection_reason is not None else None,
        "pdf_page_count": int(page_count) if page_count is not None else None,
        "extracted_char_count": int(extracted_char_count) if extracted_char_count is not None else None,
        "extraction_provider": str(extraction_provider).strip() if extraction_provider is not None else None,
        "textract_used": int(textract_used or 0),
        "textract_attempted": int(textract_attempted or 0),
        "textract_error_type": str(textract_error_type).strip() if textract_error_type is not None else None,
        "native_text_detected": int(native_text_detected or 0),
    }


def _resolve_effective_ocr_observability(
    *,
    parse_meta: dict[str, str | int | float | None],
    ocr_pages_processed: int,
    default_ocr_engine: str,
) -> tuple[bool, bool, str]:
    textract_used = int(parse_meta.get("textract_used", 0) or 0) > 0
    local_ocr_used = max(0, int(ocr_pages_processed or 0)) > 0
    effective_ocr_used = textract_used or local_ocr_used
    if textract_used:
        provider = str(parse_meta.get("extraction_provider") or "").strip()
        return effective_ocr_used, True, provider or "aws_textract"
    return effective_ocr_used, local_ocr_used, default_ocr_engine


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
    parse_observability = dict(getattr(exc, "_parse_observability", {}) or {})
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
    for key in ("inline_candidates", "tabular_candidates", "columnar_candidates", "multiline_candidates"):
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
        diagnostics["multiline_candidates"] = parser_metrics.get("multiline_candidates", 0)
    for key in ("textract_attempted", "textract_used", "native_text_detected"):
        if key in parse_observability:
            diagnostics[key] = int(parse_observability.get(key) or 0)
    textract_error_type = str(parse_observability.get("textract_error_type") or "").strip()
    if textract_error_type:
        diagnostics["textract_error_type"] = textract_error_type
    return diagnostics


def _resolve_consumed_units(identity, analysis) -> int:
    if getattr(identity, "quota_mode", "conversion") != "pages":
        return 1
    pages_count = _resolve_processed_pages(analysis)
    return pages_count if pages_count is not None else 1
