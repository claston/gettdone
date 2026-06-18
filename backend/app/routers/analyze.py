from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app.application import (
    AccessControlService,
    AnalyzeDocumentRunner,
    InvalidFileContentError,
    InvalidUserTokenError,
    ReportService,
    UnsupportedFileTypeError,
)
from app.dependencies import get_access_control_service, get_analyze_document, get_report_service
from app.schemas import AnalyzeResponse

router = APIRouter()


def _resolve_user_token(*, authorization: str | None, user_token_form: str | None) -> str | None:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    clean_form = (user_token_form or "").strip()
    return clean_form or None


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    analyze_document: AnalyzeDocumentRunner = Depends(get_analyze_document),
    report_service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AnalyzeResponse:
    try:
        data = await file.read()
        analysis = analyze_document(filename=file.filename or "", raw_bytes=data)
        resolved_user_token = _resolve_user_token(authorization=authorization, user_token_form=user_token)
        has_identity_hint = bool((anonymous_fingerprint or "").strip() or resolved_user_token)
        if has_identity_hint:
            identity = access_control_service.resolve_identity(
                anonymous_fingerprint=anonymous_fingerprint,
                user_token=resolved_user_token,
            )
            report_service.set_report_owner(
                analysis_id=analysis.analysis_id,
                identity_type=identity.identity_type,
                identity_id=identity.identity_id,
            )
        return analysis
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
