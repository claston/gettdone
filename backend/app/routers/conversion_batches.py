from __future__ import annotations

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.application import AccessControlService, InvalidUserTokenError, QuotaExceededError
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.contracts.batches import ConversionBatchSnapshot
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
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_service,
    get_conversion_runtime_config,
)
from app.routers.auth_session import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    resolve_anonymous_fingerprint_with_cookie,
    resolve_user_token_with_session,
)

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


class ConversionBatchResponse(BaseModel):
    batch_id: str
    status: str
    created: bool | None = None
    uploads: list[ConversionUploadResponse] | None = None
    jobs: list[ConversionBatchJobResponse]


class ConversionRuntimeResponse(BaseModel):
    architecture_mode: str
    upload_mode: str
    execution_mode: str
    batch_max_files: int
    direct_batch_enabled: bool
    fallback_endpoint: str


@router.get("/api/conversion-runtime", response_model=ConversionRuntimeResponse)
def conversion_runtime(
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConversionRuntimeResponse:
    identity = _resolve_optional_registered_identity(
        access_control_service=access_control_service,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
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
        batch_max_files=effective_runtime.batch_max_files,
        direct_batch_enabled=_direct_batch_enabled(effective_runtime),
        fallback_endpoint="/api/conversions/upload",
    )


@router.post("/api/conversion-batches", response_model=ConversionBatchResponse, status_code=201)
def create_conversion_batch(
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
    active_service = _require_direct_batch(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
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
def submit_conversion_batch(
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
    active_service = _require_direct_batch(
        runtime,
        service,
        rollout_policy=rollout_policy,
        identity=identity,
        access_control_service=access_control_service,
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


@router.get("/api/conversion-batches/{batch_id}", response_model=ConversionBatchResponse)
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


def _resolve_optional_registered_identity(
    *,
    access_control_service: AccessControlService,
    authorization: str | None,
    access_cookie_token: str | None,
):
    try:
        user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=None,
            access_cookie_token=access_cookie_token,
        )
        if not user_token:
            return None
        return access_control_service.resolve_identity(
            anonymous_fingerprint=None,
            user_token=user_token,
        )
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
    if rollout_policy.allows(identity=identity, access_control_service=access_control_service):
        return ConversionRuntimeConfig(
            architecture_mode=ConversionArchitectureMode.ASYNC_AWS,
            upload_mode=ConversionUploadMode.DIRECT_S3,
            execution_mode=ConversionExecutionMode.SQS_LAMBDA,
            batch_max_files=runtime.batch_max_files,
        )
    return runtime


def _require_direct_batch(
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
    if not _direct_batch_enabled(effective_runtime) or service is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "async_conversion_disabled",
                "message": "A conversão assíncrona está desativada; use o fluxo atual.",
                "fallback_endpoint": "/api/conversions/upload",
            },
        )
    return service


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
        jobs=[
            ConversionBatchJobResponse(
                job_id=record.job.job_id,
                filename=record.job.document.filename,
                status=record.status.value,
                analysis_id=record.result.analysis_id if record.result else None,
                error_code=record.failure.code if record.failure else None,
                error_message=record.failure.message if record.failure else None,
                result=record.result.payload if record.result else None,
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
