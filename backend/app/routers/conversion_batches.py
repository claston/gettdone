from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.application import AccessControlService, InvalidUserTokenError, QuotaExceededError, ReportService
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.contracts.batches import ConversionBatchSnapshot
from app.application.conversion.contracts.documents import ConversionDocumentStore
from app.application.conversion.conversion_batch_service import (
    ConversionBatchFile,
    ConversionBatchService,
    PreparedConversionBatch,
)
from app.application.conversion.conversion_job_factory import resolve_conversion_identity
from app.application.conversion.conversion_runtime_config import (
    ConversionArchitectureMode,
    ConversionExecutionMode,
    ConversionRuntimeConfig,
    ConversionUploadMode,
)
from app.application.errors import AnalysisNotFoundError
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_document_store,
    get_conversion_batch_service,
    get_conversion_runtime_config,
    get_report_service,
)
from app.routers.auth_session import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    resolve_anonymous_fingerprint_with_cookie,
    resolve_user_token_with_session,
)
from app.routers.pro_security import require_pro_browser_request

router = APIRouter()
logger = logging.getLogger(__name__)


class ConversionBatchFileRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=150)
    size_bytes: int = Field(ge=1)
    sha256_hex: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class CreateConversionBatchRequest(BaseModel):
    files: list[ConversionBatchFileRequest] = Field(min_length=1, max_length=12)
    anonymous_fingerprint: str | None = Field(default=None, max_length=500)
    user_token: str | None = Field(default=None, max_length=500)


class ConversionBatchIdentityRequest(BaseModel):
    anonymous_fingerprint: str | None = Field(default=None, max_length=500)
    user_token: str | None = Field(default=None, max_length=500)


class ConversionUploadResponse(BaseModel):
    job_id: str
    filename: str
    url: str
    fields: dict[str, str]
    expires_in_seconds: int


class ConversionBatchJobResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    analysis_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, object] | None = None
    reviewed_at: str | None = None


class ConversionJobReviewResponse(BaseModel):
    job_id: str
    reviewed_at: str


class ConversionBatchResponse(BaseModel):
    batch_id: str
    status: str
    created_at: str | None = None
    created: bool | None = None
    uploads: list[ConversionUploadResponse] | None = None
    jobs: list[ConversionBatchJobResponse]


class ConversionBatchSummaryResponse(BaseModel):
    batch_id: str
    status: str
    created_at: datetime
    expires_at: datetime
    is_expired: bool
    files_count: int
    completed_count: int
    failed_count: int
    pending_count: int
    first_job_id: str | None


class ConversionBatchListResponse(BaseModel):
    items: list[ConversionBatchSummaryResponse]
    next_cursor: str | None = None


class ConversionRuntimeResponse(BaseModel):
    architecture_mode: str
    upload_mode: str
    execution_mode: str
    batch_max_files: int
    direct_batch_enabled: bool
    fallback_endpoint: str


def get_runtime_access_control_service(
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
) -> AccessControlService | None:
    if not _direct_batch_enabled(runtime) and not rollout_policy.enabled:
        return None
    return get_access_control_service()


@router.get("/api/conversion-runtime", response_model=ConversionRuntimeResponse)
def conversion_runtime(
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    access_control_service: AccessControlService | None = Depends(get_runtime_access_control_service),
) -> ConversionRuntimeResponse:
    effective_runtime = runtime
    identity = None
    if access_control_service is not None:
        identity = _resolve_optional_identity(
            access_control_service=access_control_service,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            anonymous_cookie_token=anonymous_cookie_token,
        )
        effective_runtime = _effective_runtime(
            runtime=runtime,
            rollout_policy=rollout_policy,
            identity=identity,
            access_control_service=access_control_service,
        )
    return ConversionRuntimeResponse(
        architecture_mode=effective_runtime.architecture_mode.value,
        upload_mode=effective_runtime.upload_mode.value,
        execution_mode=effective_runtime.execution_mode.value,
        batch_max_files=_runtime_batch_max_files(effective_runtime, identity),
        direct_batch_enabled=_direct_batch_enabled(effective_runtime),
        fallback_endpoint="/api/conversions/upload",
    )


@router.post("/api/conversion-batches", response_model=ConversionBatchResponse, status_code=201)
@router.post(
    "/api/pro/conversion-batches",
    response_model=ConversionBatchResponse,
    status_code=201,
    dependencies=[Depends(require_pro_browser_request)],
)
def create_conversion_batch(
    http_request: Request,
    request: CreateConversionBatchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionBatchResponse:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=request.anonymous_fingerprint,
        user_token=request.user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    _require_batch_entitlement(
        identity,
        files_count=len(request.files),
        pro_endpoint=_is_pro_batch_request(http_request),
    )
    active_service = _require_direct_batch(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
        files_count=len(request.files),
    )
    try:
        required_units = len(request.files) if identity.quota_mode == "conversion" else 1
        access_control_service.ensure_quota_available(identity, required_units=required_units)
        prepared = active_service.create(
            identity=identity,
            files=[ConversionBatchFile(**item.model_dump()) for item in request.files],
            idempotency_key=idempotency_key,
        )
    except QuotaExceededError as exc:
        quota_code = "monthly_pages_quota_exceeded" if identity.quota_mode == "pages" else "weekly_quota_exceeded"
        raise HTTPException(
            status_code=429,
            detail={
                "code": quota_code,
                "message": "O limite do plano foi atingido.",
                "identity_type": identity.identity_type,
                "quota_mode": identity.quota_mode,
                "quota_limit": identity.quota_limit,
                "quota_remaining": 0,
                "reset_at": access_control_service.get_quota_reset_at(identity),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _log_batch_error("conversion_batch_create_failed", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "conversion_batch_temporarily_unavailable", "message": "Tente novamente em alguns segundos."},
            headers={"Retry-After": "5"},
        ) from exc
    _log_batch_event(
        "conversion_batch_created" if prepared.created else "conversion_batch_reused",
        batch_id=prepared.snapshot.batch.batch_id,
        status=prepared.snapshot.batch.status.value,
        files_count=len(prepared.snapshot.jobs),
    )
    return _prepared_response(prepared)


@router.post("/api/conversion-batches/{batch_id}/submit", response_model=ConversionBatchResponse, status_code=202)
@router.post(
    "/api/pro/conversion-batches/{batch_id}/submit",
    response_model=ConversionBatchResponse,
    status_code=202,
    dependencies=[Depends(require_pro_browser_request)],
)
def submit_conversion_batch(
    http_request: Request,
    batch_id: str,
    request: ConversionBatchIdentityRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionBatchResponse:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=request.anonymous_fingerprint,
        user_token=request.user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    if _is_pro_batch_request(http_request):
        _require_batch_entitlement(identity, pro_endpoint=True)
    active_service = _require_direct_batch(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    existing = active_service.repository.get_for_owner(
        batch_id,
        identity_type=identity.identity_type,
        identity_id=identity.identity_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Conversion batch not found.")
    _require_batch_entitlement(
        identity,
        files_count=len(existing.jobs),
        pro_endpoint=_is_pro_batch_request(http_request),
    )
    trace_id = (x_request_id or "").strip()[:128] or f"trace-{uuid4().hex}"
    try:
        snapshot = active_service.submit(batch_id=batch_id, identity=identity, trace_id=trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Conversion batch not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        _log_batch_error("conversion_batch_submit_failed", exc, batch_id=batch_id)
        error_response = getattr(exc, "response", None)
        error_payload = error_response.get("Error") if isinstance(error_response, dict) else {}
        error_code = str(error_payload.get("Code") or "") if isinstance(error_payload, dict) else ""
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "conversion_upload_not_ready", "message": "Um ou mais uploads ainda não foram concluídos."},
            ) from exc
        raise HTTPException(
            status_code=503,
            detail={"code": "conversion_queue_temporarily_unavailable", "message": "Tente enviar o lote novamente."},
            headers={"Retry-After": "5"},
        ) from exc
    _log_batch_event(
        "conversion_batch_submitted",
        batch_id=snapshot.batch.batch_id,
        status=snapshot.batch.status.value,
        files_count=len(snapshot.jobs),
    )
    return _snapshot_response(snapshot)


@router.get("/api/pro/conversion-batches", response_model=ConversionBatchListResponse)
def list_conversion_batches(
    response: Response,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=500),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionBatchListResponse:
    identity = _resolve_identity(access_control_service=access_control_service, anonymous_cookie_token=None,
                                 anonymous_fingerprint=None, user_token=None, authorization=authorization,
                                 access_cookie_token=access_cookie_token)
    if identity.identity_type != "user":
        raise HTTPException(status_code=401, detail="An authenticated user session is required.")
    active = _require_async_service(runtime, service, rollout_policy=rollout_policy,
                                   identity=identity, access_control_service=access_control_service)
    before = None
    if cursor:
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            created, batch_id = json.loads(raw)
            created_at = datetime.fromisoformat(created)
            if created_at.tzinfo is None or not re.fullmatch(r"batch_[A-Za-z0-9_-]{1,80}", batch_id):
                raise ValueError("Invalid cursor")
            before = (created_at.astimezone(timezone.utc), batch_id)
        except (ValueError, TypeError, binascii.Error):
            raise HTTPException(status_code=400, detail="Invalid pagination cursor.") from None
    summaries = active.repository.list_for_owner(identity_type=identity.identity_type, identity_id=identity.identity_id,
                                                 limit=limit + 1, before=before)
    now = datetime.now(timezone.utc)
    items = []
    for summary in summaries[:limit]:
        expired = summary.expires_at <= now or summary.first_job_id is None
        items.append(ConversionBatchSummaryResponse(
            batch_id=summary.batch_id, status="expired" if expired else summary.status,
            created_at=summary.created_at, expires_at=summary.expires_at, is_expired=expired,
            files_count=summary.files_count, completed_count=summary.completed_count,
            failed_count=summary.failed_count, pending_count=summary.pending_count,
            first_job_id=None if expired else summary.first_job_id,
        ))
    next_cursor = None
    if len(summaries) > limit:
        last = summaries[limit - 1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.batch_id]).encode()).decode().rstrip("=")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return ConversionBatchListResponse(items=items, next_cursor=next_cursor)


@router.get("/api/conversion-batches/{batch_id}", response_model=ConversionBatchResponse)
@router.get("/api/pro/conversion-batches/{batch_id}", response_model=ConversionBatchResponse)
def get_conversion_batch(
    batch_id: str,
    anonymous_fingerprint: str | None = Query(default=None, max_length=500),
    user_token: str | None = Query(default=None, max_length=500),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionBatchResponse:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    active_service = _require_async_service(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    snapshot = active_service.repository.get_for_owner(
        batch_id,
        identity_type=identity.identity_type,
        identity_id=identity.identity_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Conversion batch not found.")
    return _snapshot_response(snapshot)


@router.get("/api/conversion-batches/{batch_id}/jobs/{job_id}/document")
@router.get("/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/document")
def get_conversion_job_document(
    batch_id: str,
    job_id: str,
    anonymous_fingerprint: str | None = Query(default=None, max_length=500),
    user_token: str | None = Query(default=None, max_length=500),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    document_store: ConversionDocumentStore = Depends(get_conversion_batch_document_store),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> Response:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    snapshot = _owned_snapshot(
        batch_id=batch_id,
        identity=identity,
        runtime=runtime,
        rollout_policy=rollout_policy,
        service=service,
        access_control_service=access_control_service,
    )
    record = next((item for item in snapshot.jobs if item.job.job_id == job_id), None)
    if (
        record is None
        or record.job.document.file_type != "pdf"
        or record.expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=404, detail="Conversion document not found.")
    try:
        document = document_store.load(record.job.document)
    except Exception as exc:
        logger.warning(
            "conversion_workspace_document_unavailable",
            extra={"batch_id": batch_id, "job_id": job_id, "error_class": type(exc).__name__},
        )
        raise HTTPException(status_code=404, detail="Conversion document not found.") from exc
    filename = Path(document.filename).name or "extrato.pdf"
    return Response(
        content=document.raw_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/api/conversion-batches/{batch_id}/jobs/{job_id}/review",
    response_model=ConversionJobReviewResponse,
)
@router.post(
    "/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/review",
    response_model=ConversionJobReviewResponse,
    dependencies=[Depends(require_pro_browser_request)],
)
def review_conversion_job(
    batch_id: str,
    job_id: str,
    request: ConversionBatchIdentityRequest,
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionJobReviewResponse:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=request.anonymous_fingerprint,
        user_token=request.user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    snapshot = _owned_snapshot(
        batch_id=batch_id,
        identity=identity,
        runtime=runtime,
        rollout_policy=rollout_policy,
        service=service,
        access_control_service=access_control_service,
    )
    if not any(item.job.job_id == job_id for item in snapshot.jobs):
        raise HTTPException(status_code=404, detail="Conversion job not found.")
    if service is None:  # protected by _owned_snapshot
        raise HTTPException(status_code=404, detail="Conversion batch not found.")
    reviewed_at = datetime.now(timezone.utc).isoformat()
    try:
        service.repository.mark_reviewed(job_id, reviewed_at=reviewed_at)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversionJobReviewResponse(job_id=job_id, reviewed_at=reviewed_at)


@router.post("/api/conversion-batches/{batch_id}/jobs/{job_id}/retry", response_model=ConversionBatchResponse)
@router.post(
    "/api/pro/conversion-batches/{batch_id}/jobs/{job_id}/retry",
    response_model=ConversionBatchResponse,
    dependencies=[Depends(require_pro_browser_request)],
)
def retry_conversion_job(
    batch_id: str,
    job_id: str,
    request: ConversionBatchIdentityRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionBatchResponse:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=request.anonymous_fingerprint,
        user_token=request.user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    active_service = _require_async_service(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    try:
        snapshot = active_service.retry(
            batch_id=batch_id,
            job_id=job_id,
            identity=identity,
            trace_id=(x_request_id or "").strip()[:128] or f"trace-{uuid4().hex}",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Conversion job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _snapshot_response(snapshot)


@router.get("/api/conversion-batches/{batch_id}/export")
@router.get("/api/pro/conversion-batches/{batch_id}/export")
def export_conversion_batch(
    batch_id: str,
    anonymous_fingerprint: str | None = Query(default=None, max_length=500),
    user_token: str | None = Query(default=None, max_length=500),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    service: ConversionBatchService | None = Depends(get_conversion_batch_service),
    report_service: ReportService = Depends(get_report_service),
    async_report_service: ReportService | None = Depends(get_async_conversion_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> Response:
    identity = _resolve_identity(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=user_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    snapshot = _owned_snapshot(
        batch_id=batch_id,
        identity=identity,
        runtime=runtime,
        rollout_policy=rollout_policy,
        service=service,
        access_control_service=access_control_service,
    )
    candidates = [item for item in (async_report_service, report_service) if item is not None]
    buffer = BytesIO()
    exported = 0
    used_names: set[str] = set()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for record in snapshot.jobs:
            if record.result is None:
                continue
            analysis = (record.result.payload or {}).get("analysis") or {}
            file_format = "xlsx" if analysis.get("financial_document") is not None else "ofx"
            report_path = _find_convert_report(
                record.result.analysis_id,
                candidates=candidates,
                file_format=file_format,
            )
            if report_path is None:
                continue
            archive_name = _unique_export_name(
                filename=record.job.document.filename,
                analysis_id=record.result.analysis_id,
                used_names=used_names,
                file_format=file_format,
            )
            archive.writestr(archive_name, report_path.read_bytes())
            exported += 1
    if exported == 0:
        raise HTTPException(status_code=409, detail="No completed conversion is available for export.")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="lote-{batch_id}.zip"',
            "X-Conversion-Count": str(exported),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _owned_snapshot(
    *,
    batch_id: str,
    identity,
    runtime: ConversionRuntimeConfig,
    rollout_policy: AsyncConversionRolloutPolicy,
    service: ConversionBatchService | None,
    access_control_service: AccessControlService,
) -> ConversionBatchSnapshot:
    active_service = _require_async_service(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    snapshot = active_service.repository.get_for_owner(
        batch_id,
        identity_type=identity.identity_type,
        identity_id=identity.identity_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Conversion batch not found.")
    return snapshot


def _find_convert_report(analysis_id: str, *, candidates: list[ReportService], file_format: str = "ofx") -> Path | None:
    for candidate in candidates:
        try:
            return candidate.get_convert_report_path(analysis_id, file_format=file_format)
        except AnalysisNotFoundError:
            continue
    return None


def _unique_export_name(*, filename: str, analysis_id: str, used_names: set[str], file_format: str = "ofx") -> str:
    stem = Path(filename).stem.strip() or analysis_id
    sanitized = "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in stem)
    base = sanitized.strip("-") or analysis_id
    candidate = f"{base}.{file_format}"
    suffix = 2
    while candidate.lower() in used_names:
        candidate = f"{base}-{suffix}.{file_format}"
        suffix += 1
    used_names.add(candidate.lower())
    return candidate


def _resolve_identity(
    *,
    access_control_service: AccessControlService,
    anonymous_cookie_token: str | None,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
):
    resolved_anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
        access_control_service=access_control_service,
        anonymous_cookie_token=anonymous_cookie_token,
        legacy_fingerprint=anonymous_fingerprint,
    )
    try:
        return resolve_conversion_identity(
            access_control_service=access_control_service,
            anonymous_fingerprint=resolved_anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
        )
    except InvalidUserTokenError as exc:
        raise HTTPException(status_code=401, detail="A valid conversion identity is required.") from exc


def _direct_batch_enabled(runtime: ConversionRuntimeConfig) -> bool:
    return (
        runtime.architecture_mode == ConversionArchitectureMode.ASYNC_AWS
        and runtime.upload_mode == ConversionUploadMode.DIRECT_S3
        and runtime.execution_mode == ConversionExecutionMode.SQS_LAMBDA
    )


def _resolve_optional_identity(
    *,
    access_control_service: AccessControlService,
    authorization: str | None,
    access_cookie_token: str | None,
    anonymous_cookie_token: str | None,
):
    try:
        user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=None,
            access_cookie_token=access_cookie_token,
        )
        if user_token:
            return access_control_service.resolve_identity(
                anonymous_fingerprint=None,
                user_token=user_token,
            )
        anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service,
            anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=None,
        )
        if anonymous_fingerprint:
            return access_control_service.resolve_identity(
                anonymous_fingerprint=anonymous_fingerprint,
                user_token=None,
            )
        return None
    except InvalidUserTokenError:
        return None


def _effective_runtime(
    *,
    runtime: ConversionRuntimeConfig,
    rollout_policy: AsyncConversionRolloutPolicy,
    identity,
    access_control_service: AccessControlService,
) -> ConversionRuntimeConfig:
    if _direct_batch_enabled(runtime):
        return runtime
    rollout_batch_max_files = rollout_policy.batch_max_files(
        identity=identity,
        access_control_service=access_control_service,
        configured_max=runtime.batch_max_files,
    )
    if rollout_batch_max_files is not None:
        return ConversionRuntimeConfig(
            architecture_mode=ConversionArchitectureMode.ASYNC_AWS,
            upload_mode=ConversionUploadMode.DIRECT_S3,
            execution_mode=ConversionExecutionMode.SQS_LAMBDA,
            batch_max_files=rollout_batch_max_files,
        )
    return runtime


def _runtime_batch_max_files(runtime: ConversionRuntimeConfig, identity) -> int:
    configured_max = max(1, int(runtime.batch_max_files))
    if not _direct_batch_enabled(runtime):
        return configured_max
    if identity is None or identity.identity_type != "user" or not identity.batch_conversion_enabled:
        return 1
    return min(configured_max, max(1, int(identity.batch_max_files)))


def _require_direct_batch(
    runtime: ConversionRuntimeConfig,
    service: ConversionBatchService | None,
    *,
    rollout_policy: AsyncConversionRolloutPolicy,
    identity,
    access_control_service: AccessControlService,
    files_count: int | None = None,
) -> ConversionBatchService:
    effective_runtime = _effective_runtime(
        runtime=runtime,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    if not _direct_batch_enabled(effective_runtime) or service is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "async_conversion_disabled",
                "message": "A conversão assíncrona está desativada; use o fluxo atual.",
                "fallback_endpoint": "/api/conversions/upload",
            },
        )
    if files_count is not None and files_count > effective_runtime.batch_max_files:
        raise HTTPException(
            status_code=400,
            detail="Percentage rollout conversions accept only one file per batch.",
        )
    return service


def _is_pro_batch_request(request: Request) -> bool:
    return request.url.path.startswith("/api/pro/conversion-batches")


def _require_batch_entitlement(
    identity,
    *,
    files_count: int | None = None,
    pro_endpoint: bool = False,
) -> None:
    is_multi_file = files_count is not None and files_count > 1
    if not pro_endpoint and not is_multi_file:
        return
    if identity.identity_type != "user" or not identity.batch_conversion_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "batch_plan_required",
                "message": "A conversão em lote requer um plano com esse recurso habilitado.",
            },
        )
    plan_batch_max_files = max(1, int(identity.batch_max_files))
    if files_count is not None and files_count > plan_batch_max_files:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "batch_file_limit_exceeded",
                "message": f"Este plano permite no máximo {plan_batch_max_files} arquivos por lote.",
                "batch_max_files": plan_batch_max_files,
            },
        )


def _require_async_service(
    runtime: ConversionRuntimeConfig,
    service: ConversionBatchService | None,
    *,
    rollout_policy: AsyncConversionRolloutPolicy,
    identity,
    access_control_service: AccessControlService,
) -> ConversionBatchService:
    effective_runtime = _effective_runtime(
        runtime=runtime,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
    )
    if effective_runtime.architecture_mode != ConversionArchitectureMode.ASYNC_AWS or service is None:
        raise HTTPException(status_code=404, detail="Conversion batch not found.")
    return service


def _prepared_response(prepared: PreparedConversionBatch) -> ConversionBatchResponse:
    response = _snapshot_response(prepared.snapshot)
    response.created = prepared.created
    response.uploads = [
        ConversionUploadResponse(
            job_id=item.job_id,
            filename=item.document.filename,
            url=item.upload_url,
            fields=item.upload_fields,
            expires_in_seconds=item.expires_in_seconds,
        )
        for item in prepared.uploads
    ]
    return response


def _snapshot_response(snapshot: ConversionBatchSnapshot) -> ConversionBatchResponse:
    return ConversionBatchResponse(
        batch_id=snapshot.batch.batch_id,
        status=snapshot.batch.status.value,
        created_at=snapshot.batch.created_at.isoformat(),
        jobs=[
            ConversionBatchJobResponse(
                job_id=record.job.job_id,
                filename=record.job.document.filename,
                status=record.status.value,
                analysis_id=record.result.analysis_id if record.result else None,
                error_code=record.failure.code if record.failure else None,
                error_message=record.failure.message if record.failure else None,
                result=record.result.payload if record.result else None,
                reviewed_at=(
                    str(record.result.payload.get("reviewed_at") or "").strip() or None
                    if record.result and record.result.payload
                    else None
                ),
            )
            for record in snapshot.jobs
        ],
    )


def _log_batch_error(event: str, exc: Exception, *, batch_id: str | None = None) -> None:
    logger.error(
        json.dumps(
            {
                "event": event,
                "batch_id": batch_id,
                "error_class": type(exc).__name__,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _log_batch_event(
    event: str,
    *,
    batch_id: str,
    status: str,
    files_count: int,
) -> None:
    logger.info(
        json.dumps(
            {
                "event": event,
                "batch_id": batch_id,
                "status": status,
                "files_count": files_count,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
