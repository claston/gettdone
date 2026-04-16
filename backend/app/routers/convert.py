from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.application import (
    AccessControlService,
    AnalyzeService,
    FileTooLargeError,
    InvalidFileContentError,
    InvalidUserTokenError,
    QuotaExceededError,
    UnsupportedFileTypeError,
)
from app.dependencies import get_access_control_service, get_analyze_service
from app.schemas import ConvertResponse

router = APIRouter()


@router.post("/convert", response_model=ConvertResponse)
async def convert(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    analyze_service: AnalyzeService = Depends(get_analyze_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertResponse:
    try:
        data = await file.read()
        access_control_service.assert_upload_size(data)
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
        )
        access_control_service.ensure_quota_available(identity)
        analysis = analyze_service.analyze(filename=file.filename or "", raw_bytes=data)
        quota_remaining = access_control_service.consume_quota(identity)
        return ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
    except FileTooLargeError:
        raise HTTPException(status_code=413, detail="File exceeds maximum size of 2 MB.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )
    except QuotaExceededError:
        raise HTTPException(
            status_code=429,
            detail="Quota exceeded. Register to unlock +10 conversions.",
        )
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
