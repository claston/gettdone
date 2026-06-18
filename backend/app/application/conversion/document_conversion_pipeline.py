import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.application.access_control import AccessControlService
from app.application.analysis_response_builder import persist_and_build_analyze_response
from app.application.bank_identity import resolve_conversion_model_label
from app.application.conversion.conversion_pipeline_result import (
    ConversionPipelineResult,
)
from app.application.conversion.document_extractor import (
    DocumentExtractor,
    LegacyParsingServiceDocumentExtractor,
)
from app.application.conversion.document_preflight_service import (
    DocumentPreflightPolicy,
    DocumentPreflightResult,
    DocumentPreflightService,
)
from app.application.conversion.quota_validator_service import QuotaValidatorService
from app.application.conversion.statement_parser import (
    LegacyExtractedDocumentStatementParser,
    StatementParser,
    resolve_legacy_parsed_statement,
)
from app.application.conversion_pipeline import ConversionPipeline
from app.application.errors import (
    FileTooLargeError,
    InvalidFileContentError,
    InvalidSessionTokenError,
    InvalidUserTokenError,
    MaxPagesPerFileExceededError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.application.ingestion import ingest_uploaded_document
from app.application.report_service import ReportService
from app.application.repositories import AnalysisRepository
from app.schemas import AnalyzeResponse, ConvertResponse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StagedUploadRef:
    path: Path
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class DocumentConversionRequest:
    filename: str
    staged_upload: StagedUploadRef
    anonymous_fingerprint: str | None
    user_token: str | None
    authorization: str | None
    access_cookie_token: str | None
    on_ocr_progress: Callable[[int, int], None] | None
    preflight_result: DocumentPreflightResult

    @classmethod
    def from_inputs(
        cls,
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
    ) -> "DocumentConversionRequest":
        return cls(
            filename=filename,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            on_ocr_progress=on_ocr_progress,
            preflight_result=DocumentPreflightResult(
                scanned_likely=bool(scanned_likely),
                estimated_pages_count=estimated_pages_count,
            ),
        )


@dataclass(slots=True)
class DocumentConversionRuntime:
    started_at: float
    file_digest: str | None
    ocr_engine: str
    identity: object | None = None
    ocr_pages_processed: int = 0
    ocr_started_logged: bool = False
    attempt_processing_id: str | None = None
    attempt_anonymous_event_id: str | None = None

    @classmethod
    def from_request(cls, request: DocumentConversionRequest) -> "DocumentConversionRuntime":
        return cls(
            started_at=monotonic(),
            file_digest=request.staged_upload.sha256_hex or None,
            ocr_engine=os.getenv("PDF_OCR_ENGINE", "").strip().lower() or "tesseract",
        )

    def build_ocr_progress_callback(self, *, request: DocumentConversionRequest):
        def telemetry_ocr_progress(current_page: int, total_page_count: int) -> None:
            if not self.ocr_started_logged:
                logger.info(
                    "conversion_ocr_started filename=%s estimated_pages_count=%s ocr_engine=%s",
                    request.filename or "unknown.pdf",
                    request.preflight_result.estimated_pages_count,
                    self.ocr_engine,
                )
                self.ocr_started_logged = True
            self.ocr_pages_processed = max(
                self.ocr_pages_processed,
                max(1, min(int(current_page or 1), max(1, int(total_page_count or 0)))),
            )
            if request.on_ocr_progress is not None:
                request.on_ocr_progress(current_page, total_page_count)

        return telemetry_ocr_progress

    def duration_ms(self) -> int:
        return int((monotonic() - self.started_at) * 1000)


@dataclass(frozen=True, slots=True)
class PreparedDocumentConversion:
    request: DocumentConversionRequest
    identity: object
    preflight_policy: DocumentPreflightPolicy
    raw_bytes: bytes


class DocumentConversionPipeline:
    def __init__(
        self,
        *,
        report_service: ReportService,
        access_control_service: AccessControlService,
        document_preflight_service: DocumentPreflightService | None = None,
        quota_validator_service: QuotaValidatorService | None = None,
        processing_pipeline: ConversionPipeline | None = None,
        analysis_repository: AnalysisRepository | None = None,
        document_extractor: DocumentExtractor | None = None,
        statement_parser: StatementParser | None = None,
        analyze_fallback_service=None,
    ) -> None:
        self.report_service = report_service
        self.access_control_service = access_control_service
        self.document_preflight_service = document_preflight_service or DocumentPreflightService()
        self.quota_validator_service = quota_validator_service or QuotaValidatorService(
            access_control_service=access_control_service
        )
        self.processing_pipeline = processing_pipeline
        self.analysis_repository = analysis_repository
        self.document_extractor = document_extractor or LegacyParsingServiceDocumentExtractor(
            parsing_service=getattr(processing_pipeline, "parser", None)
        )
        self.statement_parser = statement_parser or LegacyExtractedDocumentStatementParser()
        self.analyze_fallback_service = analyze_fallback_service

    def run(
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
    ) -> ConversionPipelineResult:
        request = DocumentConversionRequest.from_inputs(
            filename=filename,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            on_ocr_progress=on_ocr_progress,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
        )
        runtime = DocumentConversionRuntime.from_request(request)
        try:
            prepared = self._prepare_conversion(request=request, runtime=runtime)
            analysis = self._execute_analysis_step(prepared=prepared, runtime=runtime)
            return self._finalize_success(prepared=prepared, runtime=runtime, analysis=analysis)
        except Exception as exc:
            self._finalize_failure(request=request, runtime=runtime, exc=exc)
            raise

    def _prepare_conversion(
        self,
        *,
        request: DocumentConversionRequest,
        runtime: DocumentConversionRuntime,
    ) -> PreparedDocumentConversion:
        resolved_user_token = _resolve_user_token_with_session(
            access_control_service=self.access_control_service,
            authorization=request.authorization,
            explicit_user_token=request.user_token,
            access_cookie_token=request.access_cookie_token,
        )
        identity = self.access_control_service.resolve_identity(
            anonymous_fingerprint=request.anonymous_fingerprint,
            user_token=resolved_user_token,
        )
        runtime.identity = identity
        logger.info(
            "conversion_analyze_precheck filename=%s identity_type=%s scanned_likely=%s estimated_pages_count=%s",
            request.filename or "unknown.pdf",
            getattr(identity, "identity_type", "unknown"),
            request.preflight_result.scanned_likely,
            request.preflight_result.estimated_pages_count,
        )
        preflight_policy = self.document_preflight_service.build_policy(
            identity=identity,
            filename=request.filename,
            staged_upload_size_bytes=request.staged_upload.size_bytes,
            preflight_result=request.preflight_result,
        )
        raw_bytes = request.staged_upload.path.read_bytes()
        try:
            self.access_control_service.assert_upload_size(
                raw_bytes,
                max_upload_size_bytes=preflight_policy.max_upload_size_bytes,
            )
        except FileTooLargeError as exc:
            setattr(exc, "_max_upload_size_bytes", preflight_policy.max_upload_size_bytes)
            raise
        self.quota_validator_service.ensure_conversion_quota_available(identity=identity)
        self._record_processing_started(request=request, runtime=runtime, identity=identity)
        return PreparedDocumentConversion(
            request=request,
            identity=identity,
            preflight_policy=preflight_policy,
            raw_bytes=raw_bytes,
        )

    def _record_processing_started(
        self,
        *,
        request: DocumentConversionRequest,
        runtime: DocumentConversionRuntime,
        identity,
    ) -> None:
        if identity.identity_type == "user":
            runtime.attempt_processing_id = f"an_{uuid4().hex[:12]}"
            _safe_record_user_conversion(
                self.access_control_service,
                user_id=identity.identity_id,
                processing_id=runtime.attempt_processing_id,
                filename=request.filename or f"{runtime.attempt_processing_id}.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(request.filename),
                status="Processando",
                transactions_count=0,
                pages_count=request.preflight_result.estimated_pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
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
                pdf_page_count=request.preflight_result.estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=False,
                ocr_engine=runtime.ocr_engine,
                file_sha256=runtime.file_digest,
                canonical_warning_transactions_count=0,
                balance_consistency_failed=0,
                expires_at=None,
            )
        elif identity.identity_type == "anonymous":
            runtime.attempt_anonymous_event_id = f"anon_evt_{uuid4().hex[:24]}"
            _safe_record_anonymous_conversion_event(
                self.access_control_service,
                event_id=runtime.attempt_anonymous_event_id,
                anonymous_fingerprint=identity.identity_id,
                filename=request.filename or "unknown.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(request.filename),
                status="Processando",
                transactions_count=None,
                pages_count=request.preflight_result.estimated_pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
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
                pdf_page_count=request.preflight_result.estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=False,
                ocr_engine=runtime.ocr_engine,
                file_sha256=runtime.file_digest,
            )

    def _execute_analysis_step(
        self,
        *,
        prepared: PreparedDocumentConversion,
        runtime: DocumentConversionRuntime,
    ) -> AnalyzeResponse:
        request = prepared.request
        preflight_result = request.preflight_result
        try:
            ocr_max_pages = prepared.preflight_policy.ocr_max_pages
            logger.info(
                (
                    "conversion_analyze_started filename=%s identity_type=%s scanned_likely=%s "
                    "estimated_pages_count=%s max_upload_size_bytes=%s ocr_max_pages=%s"
                ),
                request.filename or "unknown.pdf",
                getattr(prepared.identity, "identity_type", "unknown"),
                preflight_result.scanned_likely,
                preflight_result.estimated_pages_count,
                prepared.preflight_policy.max_upload_size_bytes,
                ocr_max_pages,
            )
            return self._analyze_document(
                filename=request.filename,
                raw_bytes=prepared.raw_bytes,
                analysis_id=runtime.attempt_processing_id,
                on_ocr_progress=runtime.build_ocr_progress_callback(request=request),
                max_ocr_pages=ocr_max_pages,
            )
        except MaxPagesPerFileExceededError as exc:
            _apply_ocr_limit_context(
                exc,
                scanned_likely=preflight_result.scanned_likely,
                document_preflight_service=self.document_preflight_service,
            )
            raise
        except InvalidFileContentError as exc:
            detail = str(exc).lower()
            if (
                Path(request.filename or "").suffix.lower() == ".pdf"
                and preflight_result.estimated_pages_count is not None
                and ("text" in detail or "ocr" in detail)
            ):
                ocr_max_pages = self.document_preflight_service.resolve_ocr_max_pages_per_file(prepared.identity)
                if int(preflight_result.estimated_pages_count) > ocr_max_pages:
                    self.document_preflight_service._log_pages_limit_exceeded_attempt(
                        identity=prepared.identity,
                        filename=request.filename,
                        pages_count=int(preflight_result.estimated_pages_count),
                        max_pages_per_file=ocr_max_pages,
                        scanned_likely=preflight_result.scanned_likely,
                    )
                    raise MaxPagesPerFileExceededError(
                        pages_count=int(preflight_result.estimated_pages_count),
                        max_pages_per_file=ocr_max_pages,
                        ocr_context=self.document_preflight_service.resolve_ocr_limit_context(
                            scanned_likely=preflight_result.scanned_likely
                        ),
                    ) from exc
            raise

    def _finalize_success(
        self,
        *,
        prepared: PreparedDocumentConversion,
        runtime: DocumentConversionRuntime,
        analysis: AnalyzeResponse,
    ) -> ConversionPipelineResult:
        request = prepared.request
        identity = prepared.identity
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
            ocr_pages_processed=runtime.ocr_pages_processed,
            default_ocr_engine=runtime.ocr_engine,
        )
        conversion_model_label = resolve_conversion_model_label(
            layout_inference_name=getattr(analysis, "layout_inference_name", None),
            bank_name=getattr(analysis, "bank_name", None),
        )
        logger.info(
            "conversion_result_persist_started filename=%s identity_type=%s status=Sucesso analysis_id=%s",
            request.filename or "unknown.pdf",
            getattr(identity, "identity_type", "unknown"),
            analysis.analysis_id,
        )
        quota_result = self.quota_validator_service.consume_quota_for_conversion(
            identity=identity,
            analysis=analysis,
        )
        quota_remaining = quota_result.quota_remaining
        if identity.identity_type == "user":
            file_type = str(analysis.file_type or "").strip().lower()
            conversion_type = f"{file_type}-ofx" if file_type else "pdf-ofx"
            self.access_control_service.record_user_conversion(
                user_id=identity.identity_id,
                processing_id=runtime.attempt_processing_id or analysis.analysis_id,
                filename=request.filename or f"{analysis.analysis_id}.pdf",
                model=conversion_model_label,
                conversion_type=conversion_type,
                status="Sucesso",
                transactions_count=int(analysis.transactions_total),
                pages_count=pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
                ocr_used=effective_ocr_used,
                ocr_pages_processed=runtime.ocr_pages_processed,
                duration_ms=runtime.duration_ms(),
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
                file_sha256=runtime.file_digest,
                canonical_warning_transactions_count=warning_rows_count,
                balance_consistency_failed=balance_failed_count,
                expires_at=analysis.expires_at,
            )
        elif identity.identity_type == "anonymous":
            _safe_record_anonymous_conversion_event(
                self.access_control_service,
                event_id=runtime.attempt_anonymous_event_id or f"anon_evt_{uuid4().hex[:24]}",
                anonymous_fingerprint=identity.identity_id,
                filename=request.filename or f"{analysis.analysis_id}.pdf",
                model=conversion_model_label,
                conversion_type=_resolve_conversion_type_from_filename(request.filename),
                status="Sucesso",
                transactions_count=int(analysis.transactions_total),
                pages_count=pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
                ocr_used=effective_ocr_used,
                ocr_pages_processed=runtime.ocr_pages_processed,
                duration_ms=runtime.duration_ms(),
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
                file_sha256=runtime.file_digest,
            )
        response = ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            quota_mode=identity.quota_mode,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
        return ConversionPipelineResult.completed(
            payload=response.model_dump(),
            metadata=_build_conversion_pipeline_metadata(
                analysis=analysis,
                scanned_likely=request.preflight_result.scanned_likely,
                quota_remaining=quota_remaining,
                ocr_used=effective_ocr_used,
                extractor_used=parse_meta["selected_parser"],
            ),
        )

    def _finalize_failure(
        self,
        *,
        request: DocumentConversionRequest,
        runtime: DocumentConversionRuntime,
        exc: Exception,
    ) -> None:
        identity = runtime.identity
        error_stage, error_subcode, exception_class = _resolve_error_observability(exc)
        error_code = _resolve_failed_conversion_code(exc)
        failure_diagnostics = _build_failure_diagnostics(exc)
        ocr_context = str(getattr(exc, "ocr_context", "") or "")
        if ocr_context:
            failure_diagnostics["ocr_fallback_attempted"] = True
            failure_diagnostics["ocr_fallback_reason"] = ocr_context
            if isinstance(exc, MaxPagesPerFileExceededError):
                failure_diagnostics["ocr_max_pages"] = exc.max_pages_per_file
        duration_ms = runtime.duration_ms()
        ocr_attempted = runtime.ocr_pages_processed > 0 or bool(ocr_context)
        failed_event_id: str | None = None
        logger.info(
            "conversion_result_persist_started filename=%s identity_type=%s status=Falha error_code=%s error_stage=%s",
            request.filename or "unknown.pdf",
            getattr(identity, "identity_type", "unknown"),
            error_code,
            error_stage or "",
        )
        if identity is not None and identity.identity_type == "anonymous":
            failed_event_id = runtime.attempt_anonymous_event_id or f"anon_evt_{uuid4().hex[:24]}"
            _safe_record_anonymous_conversion_event(
                self.access_control_service,
                event_id=failed_event_id,
                anonymous_fingerprint=identity.identity_id,
                filename=request.filename or "unknown.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(request.filename),
                status="Falha",
                transactions_count=None,
                pages_count=request.preflight_result.estimated_pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
                ocr_used=runtime.ocr_pages_processed > 0,
                ocr_pages_processed=runtime.ocr_pages_processed,
                duration_ms=duration_ms,
                error_code=error_code,
                error_stage=error_stage,
                error_subcode=error_subcode,
                exception_class=exception_class,
                layout_inference_name=None,
                layout_inference_confidence=None,
                selected_parser=None,
                parser_selection_reason=None,
                pdf_page_count=request.preflight_result.estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=ocr_attempted,
                ocr_engine=runtime.ocr_engine,
                file_sha256=runtime.file_digest,
                canonical_warning_transactions_count=0,
                balance_consistency_failed=0,
            )
        elif identity is not None and identity.identity_type == "user":
            _safe_record_user_conversion(
                self.access_control_service,
                user_id=identity.identity_id,
                processing_id=runtime.attempt_processing_id or f"an_{uuid4().hex[:12]}",
                filename=request.filename or "unknown.pdf",
                model="Nao identificado",
                conversion_type=_resolve_conversion_type_from_filename(request.filename),
                status="Falha",
                transactions_count=0,
                pages_count=request.preflight_result.estimated_pages_count,
                scanned_likely=request.preflight_result.scanned_likely,
                ocr_used=runtime.ocr_pages_processed > 0,
                ocr_pages_processed=runtime.ocr_pages_processed,
                duration_ms=duration_ms,
                error_code=error_code,
                error_stage=error_stage,
                error_subcode=error_subcode,
                exception_class=exception_class,
                layout_inference_name=None,
                layout_inference_confidence=None,
                selected_parser=None,
                parser_selection_reason=None,
                pdf_page_count=request.preflight_result.estimated_pages_count,
                extracted_char_count=None,
                ocr_attempted=ocr_attempted,
                ocr_engine=runtime.ocr_engine,
                file_sha256=runtime.file_digest,
                canonical_warning_transactions_count=0,
                balance_consistency_failed=0,
                expires_at=None,
            )
        _log_conversion_failure(
            identity=identity,
            filename=request.filename or "unknown.pdf",
            event_id=failed_event_id,
            error_code=error_code,
            error_stage=error_stage,
            error_subcode=error_subcode,
            exception_class=exception_class,
            scanned_likely=request.preflight_result.scanned_likely,
            estimated_pages_count=request.preflight_result.estimated_pages_count,
            ocr_pages_processed=runtime.ocr_pages_processed,
            duration_ms=duration_ms,
            failure_diagnostics=failure_diagnostics,
        )
        setattr(exc, "_convert_identity", identity)

    def _analyze_document(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        analysis_id: str | None,
        on_ocr_progress,
        max_ocr_pages: int | None,
    ) -> AnalyzeResponse:
        if self.processing_pipeline is not None and self.analysis_repository is not None:
            document = ingest_uploaded_document(filename=filename, raw_bytes=raw_bytes)
            logger.info(
                "analyze_start extension=%s size_bytes=%d filename=%s",
                document.file_type,
                document.size_bytes,
                (filename or "")[:120],
            )
            resolved_analysis_id = (analysis_id or "").strip() or f"an_{uuid4().hex[:12]}"
            parse_started_at = monotonic()
            extracted_document = self.document_extractor.extract(
                document=document,
                on_ocr_progress=on_ocr_progress,
                max_ocr_pages=max_ocr_pages,
            )
            parsed_statement = self.statement_parser.parse(extracted_document=extracted_document)
            parse_ms = round((monotonic() - parse_started_at) * 1000, 3)
            pipeline_result = self.processing_pipeline.run_parsed_document(
                document=document,
                parsed_document=resolve_legacy_parsed_statement(parsed_statement),
                analysis_id=resolved_analysis_id,
                parse_ms=parse_ms,
            )
            analysis = persist_and_build_analyze_response(
                storage=self.analysis_repository,
                pipeline_result=pipeline_result,
            )
            logger.info(
                "analyze_done analysis_id=%s extension=%s total_ms=%.3f parse_ms=%.3f tx_count=%d layout=%s parser=%s",
                analysis.analysis_id,
                document.file_type,
                _metrics_get(analysis.pdf_processing_metrics, "total_ms", 0.0),
                pipeline_result.parse_ms,
                analysis.transactions_total,
                analysis.layout_inference_name or "",
                _metrics_get(analysis.pdf_processing_metrics, "selected_parser", ""),
            )
            return analysis
        if self.analyze_fallback_service is None:
            raise RuntimeError("DocumentConversionPipeline requires a processing pipeline or analyze fallback service.")
        return self.analyze_fallback_service.analyze(
            filename=filename,
            raw_bytes=raw_bytes,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            analysis_id=analysis_id,
        )


def _resolve_processed_pages(analysis) -> int | None:
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    if metrics is None:
        return None
    page_count = (
        int(metrics.get("page_count", 0) or 0)
        if isinstance(metrics, dict)
        else int(getattr(metrics, "page_count", 0) or 0)
    )
    return max(1, page_count)


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


def _apply_ocr_limit_context(
    exc: MaxPagesPerFileExceededError,
    *,
    scanned_likely: bool | None,
    document_preflight_service: DocumentPreflightService,
) -> None:
    if not getattr(exc, "ocr_context", None):
        exc.ocr_context = document_preflight_service.resolve_ocr_limit_context(scanned_likely=scanned_likely)


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


def _resolve_conversion_type_from_filename(filename: str) -> str:
    extension = Path(filename or "").suffix.lower().strip(".")
    return f"{extension}-ofx" if extension else "pdf-ofx"


def _build_conversion_pipeline_metadata(
    *,
    analysis,
    scanned_likely: bool | None,
    quota_remaining: int,
    ocr_used: bool,
    extractor_used: str | int | float | None,
) -> dict[str, str | int | bool | None]:
    parse_meta = _resolve_parse_observability_metrics(analysis)
    return {
        "page_count": _resolve_processed_pages(analysis),
        "is_scanned": scanned_likely,
        "required_quota": 1,
        "remaining_quota": quota_remaining,
        "extractor_used": str(extractor_used).strip() if extractor_used is not None else None,
        "ocr_used": ocr_used,
        "extractor_provider": parse_meta["extraction_provider"],
    }


def _metrics_get(metrics, key: str, default):
    if metrics is None:
        return default
    if isinstance(metrics, dict):
        return metrics.get(key, default)
    return getattr(metrics, key, default)
