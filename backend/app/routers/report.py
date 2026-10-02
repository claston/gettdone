import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from app.application import (
    AccessControlService,
    AnalysisAccessDeniedError,
    AnalysisEditConflictError,
    AnalysisNotFoundError,
    InvalidUserTokenError,
    ReportService,
)
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.conversion_runtime_config import (
    ConversionArchitectureMode,
    ConversionRuntimeConfig,
)
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_async_conversion_rollout_policy,
    get_conversion_runtime_config,
    get_report_service,
)
from app.routers.auth_session import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    resolve_anonymous_fingerprint_with_cookie,
    resolve_user_token_with_session,
)
from app.schemas import ConvertEditsRequest, ConvertEditsResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _record_download_safely(
    access_control_service: AccessControlService,
    *,
    identity,
    processing_id: str,
    download_format: str,
) -> None:
    try:
        access_control_service.record_product_event(
            identity=identity,
            event_type="download",
            processing_id=processing_id,
            download_format=download_format,
        )
    except Exception:
        logger.warning(
            "product_download_telemetry_failed processing_id=%s format=%s",
            processing_id,
            download_format,
            exc_info=True,
        )

@router.get("/report/{analysis_id}")
def get_report(
    analysis_id: str,
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> FileResponse:
    try:
        resolved_user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=user_token,
            access_cookie_token=access_cookie_token,
        )
        resolved_anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service,
            anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=anonymous_fingerprint,
        )
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=resolved_anonymous_fingerprint,
            user_token=resolved_user_token or None,
        )
        service.assert_report_owner(
            analysis_id=analysis_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
            allow_unowned=True,
        )
        report_path = service.get_report_path(analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Start an anonymous session or authenticate.",
        )

    _record_download_safely(
        access_control_service,
        identity=identity,
        processing_id=analysis_id,
        download_format="xlsx",
    )
    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"ofxsimples_report_{analysis_id}.xlsx",
    )


@router.get("/reconcile-report/{analysis_id}")
def get_reconcile_report(
    analysis_id: str,
    file_format: Literal["xlsx", "csv"] = Query(default="xlsx", alias="format"),
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> FileResponse:
    try:
        resolved_user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=user_token,
            access_cookie_token=access_cookie_token,
        )
        resolved_anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service,
            anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=anonymous_fingerprint,
        )
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=resolved_anonymous_fingerprint,
            user_token=resolved_user_token or None,
        )
        service.assert_reconcile_owner(
            analysis_id=analysis_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
            allow_unowned=True,
        )
        report_path = service.get_reconcile_report_path(analysis_id=analysis_id, file_format=file_format)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Start an anonymous session or authenticate.",
        )

    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if file_format == "xlsx"
        else "text/csv; charset=utf-8"
    )
    filename = f"ofxsimples_reconcile_{analysis_id}.{file_format}"
    _record_download_safely(
        access_control_service,
        identity=identity,
        processing_id=analysis_id,
        download_format=file_format,
    )
    return FileResponse(path=report_path, media_type=media_type, filename=filename)


@router.get("/convert-report/{processing_id}")
def get_convert_report(
    processing_id: str,
    file_format: Literal["ofx", "csv", "xlsx"] = Query(default="ofx", alias="format"),
    closing_balance: float | None = Query(default=None),
    bank_branch: str | None = Query(default=None),
    account_number: str | None = Query(default=None),
    bank_code: str | None = Query(default=None),
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    service: ReportService = Depends(get_report_service),
    async_service: ReportService | None = Depends(get_async_conversion_report_service),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> FileResponse:
    try:
        resolved_user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=user_token,
            access_cookie_token=access_cookie_token,
        )
        resolved_anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service,
            anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=anonymous_fingerprint,
        )
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=resolved_anonymous_fingerprint,
            user_token=resolved_user_token or None,
        )
        active_service = _select_convert_report_service(
            analysis_id=processing_id,
            legacy_service=service,
            async_service=async_service,
            runtime=runtime,
            rollout_policy=rollout_policy,
            identity=identity,
            access_control_service=access_control_service,
        )
        report_path = active_service.get_convert_report_path(
            processing_id,
            file_format=file_format,
            closing_balance=closing_balance if file_format == "ofx" else None,
            bank_branch=bank_branch if file_format == "ofx" else None,
            account_number=account_number if file_format == "ofx" else None,
            bank_code=bank_code if file_format == "ofx" else None,
        )
        upload_filename = active_service.get_upload_filename(processing_id)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Start an anonymous session or authenticate.",
        )

    if file_format == "ofx":
        media_type = "application/x-ofx"
    elif file_format == "xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media_type = "text/csv; charset=utf-8"
    download_filename = _build_convert_download_filename(
        analysis_id=processing_id,
        upload_filename=upload_filename,
        file_format=file_format,
    )
    _record_download_safely(
        access_control_service,
        identity=identity,
        processing_id=processing_id,
        download_format=file_format,
    )
    return FileResponse(
        path=report_path,
        media_type=media_type,
        filename=download_filename,
    )


@router.post("/convert-edits/{processing_id}", response_model=ConvertEditsResponse)
def apply_convert_edits(
    processing_id: str,
    payload: ConvertEditsRequest,
    service: ReportService = Depends(get_report_service),
    async_service: ReportService | None = Depends(get_async_conversion_report_service),
    runtime: ConversionRuntimeConfig = Depends(get_conversion_runtime_config),
    rollout_policy: AsyncConversionRolloutPolicy = Depends(get_async_conversion_rollout_policy),
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertEditsResponse:
    try:
        resolved_user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=user_token,
            access_cookie_token=access_cookie_token,
        )
        resolved_anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
            access_control_service=access_control_service,
            anonymous_cookie_token=anonymous_cookie_token,
            legacy_fingerprint=anonymous_fingerprint,
        )
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=resolved_anonymous_fingerprint,
            user_token=resolved_user_token or None,
        )
        active_service = _select_convert_report_service(
            analysis_id=processing_id,
            legacy_service=service,
            async_service=async_service,
            runtime=runtime,
            rollout_policy=rollout_policy,
            identity=identity,
            access_control_service=access_control_service,
        )
        result = active_service.apply_convert_edits(
            analysis_id=processing_id,
            edits=[item.model_dump() for item in payload.edits],
            expected_updated_at=payload.expected_updated_at,
            opening_balance=payload.opening_balance,
            closing_balance=payload.closing_balance,
            bank_branch=payload.bank_branch,
            account_number=payload.account_number,
            bank_code=payload.bank_code,
        )
        return ConvertEditsResponse(**result)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    except AnalysisEditConflictError:
        raise HTTPException(status_code=409, detail="Analysis changed since last load. Refresh and try again.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Start an anonymous session or authenticate.",
        )


def _select_convert_report_service(
    *,
    analysis_id: str,
    legacy_service: ReportService,
    async_service: ReportService | None,
    runtime: ConversionRuntimeConfig,
    rollout_policy: AsyncConversionRolloutPolicy,
    identity,
    access_control_service: AccessControlService,
) -> ReportService:
    use_async = runtime.architecture_mode == ConversionArchitectureMode.ASYNC_AWS or rollout_policy.allows(
        identity=identity,
        access_control_service=access_control_service,
    )
    candidates: list[ReportService] = []
    if use_async and async_service is not None:
        candidates.append(async_service)
    if all(candidate is not legacy_service for candidate in candidates):
        candidates.append(legacy_service)

    for candidate in candidates:
        try:
            candidate.assert_convert_owner(
                analysis_id=analysis_id,
                identity_type=identity.identity_type,
                identity_id=identity.identity_id,
            )
            return candidate
        except AnalysisNotFoundError:
            continue
    raise AnalysisNotFoundError


def _build_convert_download_filename(analysis_id: str, upload_filename: str | None, file_format: str) -> str:
    if upload_filename:
        safe_name = Path(upload_filename).name.strip()
        stem = Path(safe_name).stem.strip()
        if stem:
            return f"{stem}.{file_format}"
    return f"ofxsimples_convert_{analysis_id}.{file_format}"
