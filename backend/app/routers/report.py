from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from app.application import (
    AccessControlService,
    AnalysisAccessDeniedError,
    AnalysisEditConflictError,
    AnalysisNotFoundError,
    InvalidUserTokenError,
    ReportService,
)
from app.dependencies import get_access_control_service, get_report_service
from app.schemas import ConvertEditsRequest, ConvertEditsResponse

router = APIRouter()


def _resolve_user_token(*, authorization: str | None, user_token_query: str | None) -> str | None:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    clean_query = (user_token_query or "").strip()
    return clean_query or None


@router.get("/report/{analysis_id}")
def get_report(
    analysis_id: str,
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    try:
        report_path = service.get_report_path(analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"ofxsimples_report_{analysis_id}.xlsx",
    )


@router.get("/reconcile-report/{analysis_id}")
def get_reconcile_report(
    analysis_id: str,
    file_format: Literal["xlsx", "csv"] = Query(default="xlsx", alias="format"),
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    try:
        report_path = service.get_reconcile_report_path(analysis_id=analysis_id, file_format=file_format)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")

    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if file_format == "xlsx"
        else "text/csv; charset=utf-8"
    )
    filename = f"ofxsimples_reconcile_{analysis_id}.{file_format}"
    return FileResponse(path=report_path, media_type=media_type, filename=filename)


@router.get("/convert-report/{processing_id}")
def get_convert_report(
    processing_id: str,
    file_format: Literal["ofx", "csv"] = Query(default="ofx", alias="format"),
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> FileResponse:
    try:
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=_resolve_user_token(authorization=authorization, user_token_query=user_token),
        )
        service.assert_convert_owner(
            analysis_id=processing_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        report_path = service.get_convert_report_path(processing_id, file_format=file_format)
        upload_filename = service.get_upload_filename(processing_id)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )

    media_type = "application/x-ofx" if file_format == "ofx" else "text/csv; charset=utf-8"
    download_filename = _build_convert_download_filename(
        analysis_id=processing_id,
        upload_filename=upload_filename,
        file_format=file_format,
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
    anonymous_fingerprint: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertEditsResponse:
    try:
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=_resolve_user_token(authorization=authorization, user_token_query=user_token),
        )
        service.assert_convert_owner(
            analysis_id=processing_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        result = service.apply_convert_edits(
            analysis_id=processing_id,
            edits=[item.model_dump() for item in payload.edits],
            expected_updated_at=payload.expected_updated_at,
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
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )


def _build_convert_download_filename(analysis_id: str, upload_filename: str | None, file_format: str) -> str:
    if upload_filename:
        safe_name = Path(upload_filename).name.strip()
        stem = Path(safe_name).stem.strip()
        if stem:
            return f"{stem}_convertido.{file_format}"
    return f"ofxsimples_convert_{analysis_id}.{file_format}"
