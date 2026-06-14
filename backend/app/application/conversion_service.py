import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.application.access_control import AccessControlService
from app.application.analyze_service import AnalyzeService
from app.application.bank_identity import resolve_conversion_model_label
from app.application.errors import (
    FileTooLargeError,
    InvalidFileContentError,
    InvalidSessionTokenError,
    InvalidUserTokenError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.application.report_service import ReportService
from app.schemas import ConvertResponse

logger = logging.getLogger(__name__)

TEXT_PDF_MAX_PAGES_PER_FILE = 250
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
OCR_PDF_MAX_PAGES_PER_FILE = 10
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
OCR_CONTEXT_SCANNED_PDF = "scanned_pdf"
OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK = "unidentified_model_fallback"


@dataclass(slots=True)
class StagedUploadRef:
    path: Path
    size_bytes: int
    sha256_hex: str


class ConversionService:
    def __init__(
        self,
        *,
        analyze_service: AnalyzeService,
        report_service: ReportService,
        access_control_service: AccessControlService,
    ) -> None:
        self.analyze_service = analyze_service
        self.report_service = report_service
        self.access_control_service = access_control_service

    def build_convert_response(
        self,
        *,
        filename: str,
        staged_upload: StagedUploadRef,
        anonymous_fingerprint: str | None,
        user_token: str | None,
        authorization: str | None,
        access_cookie_token: str | None,
        on_ocr_progress=None,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
    ) -> ConvertResponse:
        identity = None
        started_at = monotonic()
        ocr_pages_processed = 0
        ocr_started_logged = False
        attempt_processing_id: str | None = None
        attempt_anonymous_event_id: str | None = None
        file_digest = staged_upload.sha256_hex or None
        ocr_engine = os.getenv("PDF_OCR_ENGINE", "").strip().lower() or "tesseract"

        def telemetry_ocr_progress(current_page: int, total_page_count: int) -> None:
            nonlocal ocr_pages_processed, ocr_started_logged
            if not ocr_started_logged:
                logger.info(
                    "conversion_ocr_started filename=%s estimated_pages_count=%s ocr_engine=%s",
                    filename or "unknown.pdf",
                    estimated_pages_count,
                    ocr_engine,
                )
                ocr_started_logged = True
            ocr_pages_processed = max(
                ocr_pages_processed,
                max(1, min(int(current_page or 1), max(1, int(total_page_count or 0)))),
            )
            if on_ocr_progress is not None:
                on_ocr_progress(current_page, total_page_count)

        try:
            resolved_user_token = _resolve_user_token_with_session(
                access_control_service=self.access_control_service,
                authorization=authorization,
                explicit_user_token=user_token,
                access_cookie_token=access_cookie_token,
            )
            identity = self.access_control_service.resolve_identity(
                anonymous_fingerprint=anonymous_fingerprint,
                user_token=resolved_user_token,
            )
            logger.info(
                "conversion_analyze_precheck filename=%s identity_type=%s scanned_likely=%s estimated_pages_count=%s",
                filename or "unknown.pdf",
                getattr(identity, "identity_type", "unknown"),
                scanned_likely,
                estimated_pages_count,
            )
            if Path(filename or "").suffix.lower() == ".pdf" and estimated_pages_count is not None:
                max_pages_per_file = _resolve_max_pages_per_file(identity, scanned_likely)
                if int(estimated_pages_count) > max_pages_per_file:
                    _log_pages_limit_exceeded_attempt(
                        identity=identity,
                        filename=filename,
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
            if staged_upload.size_bytes > max_upload_size_bytes:
                raise _build_file_too_large_error(max_upload_size_bytes)
            data = staged_upload.path.read_bytes()
            try:
                self.access_control_service.assert_upload_size(data, max_upload_size_bytes=max_upload_size_bytes)
            except FileTooLargeError as exc:
                setattr(exc, "_max_upload_size_bytes", max_upload_size_bytes)
                raise
            self.access_control_service.ensure_quota_available(identity, required_units=1)
            if identity.identity_type == "user":
                attempt_processing_id = f"an_{uuid4().hex[:12]}"
                _safe_record_user_conversion(
                    self.access_control_service,
                    user_id=identity.identity_id,
                    processing_id=attempt_processing_id,
                    filename=filename or f"{attempt_processing_id}.pdf",
                    model="Nao identificado",
                    conversion_type=_resolve_conversion_type_from_filename(filename),
                    status="Processando",
                    transactions_count=0,
                    pages_count=estimated_pages_count,
                    scanned_likely=scanned_likely,
                    ocr_used=False,
                    ocr_pages_processed=0,
                    duration_ms=0,
                    error_code=None,
                    error_stage=None,
                    error_subcode=None,
                    exception_class=None,
                    layout_inference_name=None,
                    layout_inference_confidence=None,
                    selected_parser=None,
                    parser_selection_reason=None,
                    pdf_page_count=estimated_pages_count,
                    extracted_char_count=None,
                    ocr_attempted=False,
                    ocr_engine=ocr_engine,
                    file_sha256=file_digest,
                    canonical_warning_transactions_count=0,
                    balance_consistency_failed=0,
                    expires_at=None,
                )
            elif identity.identity_type == "anonymous":
                attempt_anonymous_event_id = f"anon_evt_{uuid4().hex[:24]}"
                _safe_record_anonymous_conversion_event(
                    self.access_control_service,
                    event_id=attempt_anonymous_event_id,
                    anonymous_fingerprint=identity.identity_id,
                    filename=filename or "unknown.pdf",
                    model="Nao identificado",
                    conversion_type=_resolve_conversion_type_from_filename(filename),
                    status="Processando",
                    transactions_count=None,
                    pages_count=estimated_pages_count,
                    scanned_likely=scanned_likely,
                    ocr_used=False,
                    ocr_pages_processed=0,
                    duration_ms=0,
                    canonical_warning_transactions_count=0,
                    balance_consistency_failed=0,
                    error_code=None,
                    error_stage=None,
                    error_subcode=None,
                    exception_class=None,
                    layout_inference_name=None,
                    layout_inference_confidence=None,
                    selected_parser=None,
                    parser_selection_reason=None,
                    pdf_page_count=estimated_pages_count,
                    extracted_char_count=None,
                    ocr_attempted=False,
                    ocr_engine=ocr_engine,
                    file_sha256=file_digest,
                )
            try:
                ocr_max_pages = _resolve_ocr_max_pages_per_file(identity)
                logger.info(
                    (
                        "conversion_analyze_started filename=%s identity_type=%s scanned_likely=%s "
                        "estimated_pages_count=%s max_upload_size_bytes=%s ocr_max_pages=%s"
                    ),
                    filename or "unknown.pdf",
                    getattr(identity, "identity_type", "unknown"),
                    scanned_likely,
                    estimated_pages_count,
                    max_upload_size_bytes,
                    ocr_max_pages,
                )
                analysis = self.analyze_service.analyze(
                    filename=filename,
                    raw_bytes=data,
                    on_ocr_progress=telemetry_ocr_progress,
                    max_ocr_pages=ocr_max_pages,
                    analysis_id=attempt_processing_id,
                )
            except MaxPagesPerFileExceededError as exc:
                _apply_ocr_limit_context(exc, scanned_likely)
                raise
            except InvalidFileContentError as exc:
                detail = str(exc).lower()
                if (
                    Path(filename or "").suffix.lower() == ".pdf"
                    and estimated_pages_count is not None
                    and ("text" in detail or "ocr" in detail)
                ):
                    ocr_max_pages = _resolve_ocr_max_pages_per_file(identity)
                    if int(estimated_pages_count) > ocr_max_pages:
                        _log_pages_limit_exceeded_attempt(
                            identity=identity,
                            filename=filename,
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
            self.report_service.set_convert_owner(
                analysis_id=analysis.analysis_id,
                identity_type=identity.identity_type,
                identity_id=identity.identity_id,
            )
            pages_count = _resolve_processed_pages(analysis)
            warning_rows_count, balance_failed_count = _resolve_warning_metrics(analysis)
            parse_meta = _resolve_parse_observability_metrics(analysis)
            effective_ocr_used, effective_ocr_attempted, effective_ocr_engine = _resolve_effective_ocr_observability(
                parse_meta=parse_meta,
                ocr_pages_processed=ocr_pages_processed,
                default_ocr_engine=ocr_engine,
            )
            conversion_model_label = resolve_conversion_model_label(
                layout_inference_name=getattr(analysis, "layout_inference_name", None),
                bank_name=getattr(analysis, "bank_name", None),
            )
            logger.info(
                "conversion_result_persist_started filename=%s identity_type=%s status=Sucesso analysis_id=%s",
                filename or "unknown.pdf",
                getattr(identity, "identity_type", "unknown"),
                analysis.analysis_id,
            )
            consumed_units = _resolve_consumed_units(identity, analysis)
            quota_remaining = self.access_control_service.consume_quota(identity, consumed_units=consumed_units)
            if identity.identity_type == "user":
                file_type = str(analysis.file_type or "").strip().lower()
                conversion_type = f"{file_type}-ofx" if file_type else "pdf-ofx"
                self.access_control_service.record_user_conversion(
                    user_id=identity.identity_id,
                    processing_id=attempt_processing_id or analysis.analysis_id,
                    filename=filename or f"{analysis.analysis_id}.pdf",
                    model=conversion_model_label,
                    conversion_type=conversion_type,
                    status="Sucesso",
                    transactions_count=int(analysis.transactions_total),
                    pages_count=pages_count,
                    scanned_likely=scanned_likely,
                    ocr_used=effective_ocr_used,
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
                    ocr_attempted=effective_ocr_attempted,
                    ocr_engine=effective_ocr_engine,
                    file_sha256=file_digest,
                    canonical_warning_transactions_count=warning_rows_count,
                    balance_consistency_failed=balance_failed_count,
                    expires_at=analysis.expires_at,
                )
            elif identity.identity_type == "anonymous":
                _safe_record_anonymous_conversion_event(
                    self.access_control_service,
                    event_id=attempt_anonymous_event_id or f"anon_evt_{uuid4().hex[:24]}",
                    anonymous_fingerprint=identity.identity_id,
                    filename=filename or f"{analysis.analysis_id}.pdf",
                    model=conversion_model_label,
                    conversion_type=_resolve_conversion_type_from_filename(filename),
                    status="Sucesso",
                    transactions_count=int(analysis.transactions_total),
                    pages_count=pages_count,
                    scanned_likely=scanned_likely,
                    ocr_used=effective_ocr_used,
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
                    ocr_attempted=effective_ocr_attempted,
                    ocr_engine=effective_ocr_engine,
                    file_sha256=file_digest,
                )
            return ConvertResponse(
                processing_id=analysis.analysis_id,
                quota_remaining=quota_remaining,
                quota_limit=identity.quota_limit,
                quota_mode=identity.quota_mode,
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
            logger.info(
                "conversion_result_persist_started filename=%s identity_type=%s status=Falha error_code=%s error_stage=%s",
                filename or "unknown.pdf",
                getattr(identity, "identity_type", "unknown"),
                error_code,
                error_stage or "",
            )
            if identity is not None and identity.identity_type == "anonymous":
                failed_event_id = attempt_anonymous_event_id or f"anon_evt_{uuid4().hex[:24]}"
                _safe_record_anonymous_conversion_event(
                    self.access_control_service,
                    event_id=failed_event_id,
                    anonymous_fingerprint=identity.identity_id,
                    filename=filename or "unknown.pdf",
                    model="Nao identificado",
                    conversion_type=_resolve_conversion_type_from_filename(filename),
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
                    canonical_warning_transactions_count=0,
                    balance_consistency_failed=0,
                )
            elif identity is not None and identity.identity_type == "user":
                _safe_record_user_conversion(
                    self.access_control_service,
                    user_id=identity.identity_id,
                    processing_id=attempt_processing_id or f"an_{uuid4().hex[:12]}",
                    filename=filename or "unknown.pdf",
                    model="Nao identificado",
                    conversion_type=_resolve_conversion_type_from_filename(filename),
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
                filename=filename or "unknown.pdf",
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


def _resolve_ocr_limit_context(scanned_likely: bool | None) -> str:
    if scanned_likely is True:
        return OCR_CONTEXT_SCANNED_PDF
    return OCR_CONTEXT_UNIDENTIFIED_MODEL_FALLBACK


def _resolve_header_or_query_token(*, authorization: str | None, query_token: str | None) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    return (query_token or "").strip()


def _resolve_user_token_with_session(
    *,
    access_control_service: AccessControlService,
    authorization: str | None,
    explicit_user_token: str | None,
    access_cookie_token: str | None,
) -> str:
    resolved_token = _resolve_header_or_query_token(
        authorization=authorization,
        query_token=explicit_user_token,
    )
    if resolved_token:
        return resolved_token

    cookie_token = (access_cookie_token or "").strip()
    if not cookie_token:
        return ""
    try:
        user = access_control_service.get_user_by_session_access_token(cookie_token)
        return user.token
    except InvalidSessionTokenError:
        raise InvalidUserTokenError from None


def _apply_ocr_limit_context(exc: MaxPagesPerFileExceededError, scanned_likely: bool | None) -> None:
    if not getattr(exc, "ocr_context", None):
        exc.ocr_context = _resolve_ocr_limit_context(scanned_likely)


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
    detail = str(exc).lower()
    if "password" in detail or "senha" in detail:
        return "password_protected_pdf"
    if "text" in detail or "ocr" in detail:
        return "insufficient_text"
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


def _resolve_max_upload_size_bytes(identity, scanned_likely: bool | None, estimated_pages_count: int | None) -> int:
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
    page_count = int(metrics.get("page_count", 0) or 0) if isinstance(metrics, dict) else int(getattr(metrics, "page_count", 0) or 0)
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
            "layout_inference_name": getattr(analysis, "layout_inference_name", None) or None,
            "layout_inference_confidence": getattr(analysis, "layout_inference_confidence", None),
            "selected_parser": None,
            "parser_selection_reason": None,
            "pdf_page_count": None,
            "extracted_char_count": None,
            "extraction_provider": None,
            "textract_used": 0,
        }
    if isinstance(metrics, dict):
        selected_parser = metrics.get("selected_parser")
        parser_selection_reason = metrics.get("parser_selection_reason")
        page_count = metrics.get("page_count")
        extracted_char_count = metrics.get("extracted_char_count")
        extraction_provider = metrics.get("extraction_provider")
        textract_used = metrics.get("textract_used")
    else:
        selected_parser = getattr(metrics, "selected_parser", None)
        parser_selection_reason = getattr(metrics, "parser_selection_reason", None)
        page_count = getattr(metrics, "page_count", None)
        extracted_char_count = getattr(metrics, "extracted_char_count", None)
        extraction_provider = getattr(metrics, "extraction_provider", None)
        textract_used = getattr(metrics, "textract_used", None)
    return {
        "layout_inference_name": getattr(analysis, "layout_inference_name", None) or None,
        "layout_inference_confidence": getattr(analysis, "layout_inference_confidence", None),
        "selected_parser": str(selected_parser).strip() if selected_parser is not None else None,
        "parser_selection_reason": str(parser_selection_reason).strip() if parser_selection_reason is not None else None,
        "pdf_page_count": int(page_count) if page_count is not None else None,
        "extracted_char_count": int(extracted_char_count) if extracted_char_count is not None else None,
        "extraction_provider": str(extraction_provider).strip() if extraction_provider is not None else None,
        "textract_used": int(textract_used or 0),
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
    detail = str(exc).lower()
    if "password" in detail or "senha" in detail:
        return "native_pdf_read", "password_protected_pdf", exception_class
    if _is_likely_corrupted_pdf_detail(detail):
        return "native_pdf_read", "corrupted_pdf", exception_class
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
    diagnostics: dict[str, str | int | bool | list[str]] = {
        "pdf_read_ok": pdf_read_ok,
        "text_extracted_likely": text_extracted,
        "missing_signals": sorted(set(missing_signals)),
        "error_detail_excerpt": detail[:240],
    }
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


def _resolve_conversion_type_from_filename(filename: str) -> str:
    extension = Path(filename or "").suffix.lower().strip(".")
    return f"{extension}-ofx" if extension else "pdf-ofx"


def _build_file_too_large_error(max_upload_size_bytes: int) -> FileTooLargeError:
    exc = FileTooLargeError()
    setattr(exc, "_max_upload_size_bytes", int(max_upload_size_bytes))
    return exc
