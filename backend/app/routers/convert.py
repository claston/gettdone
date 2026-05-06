from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.application import (
    AccessControlService,
    AnalysisAccessDeniedError,
    AnalysisNotFoundError,
    AnalyzeService,
    FileTooLargeError,
    InvalidFileContentError,
    InvalidUserTokenError,
    QuotaExceededError,
    ReportService,
    UnsupportedFileTypeError,
)
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.schemas import ConvertResponse

router = APIRouter()


def _resolve_processed_pages(analysis) -> int | None:
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    if metrics is None:
        return None
    page_count = int(getattr(metrics, "page_count", 0) or 0)
    return max(1, page_count)


def _resolve_consumed_units(identity, analysis) -> int:
    if getattr(identity, "quota_mode", "conversion") != "pages":
        return 1
    pages_count = _resolve_processed_pages(analysis)
    return pages_count if pages_count is not None else 1


@router.post("/convert", response_model=ConvertResponse)
async def convert(
    file: UploadFile = File(...),
    anonymous_fingerprint: str | None = Form(default=None),
    user_token: str | None = Form(default=None),
    analyze_service: AnalyzeService = Depends(get_analyze_service),
    report_service: ReportService = Depends(get_report_service),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ConvertResponse:
    identity = None
    try:
        data = await file.read()
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
        )
        access_control_service.assert_upload_size(data, max_upload_size_bytes=identity.max_upload_size_bytes)
        access_control_service.ensure_quota_available(identity, required_units=1)
        analysis = analyze_service.analyze(filename=file.filename or "", raw_bytes=data)
        report_service.set_convert_owner(
            analysis_id=analysis.analysis_id,
            identity_type=identity.identity_type,
            identity_id=identity.identity_id,
        )
        pages_count = _resolve_processed_pages(analysis)
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
                expires_at=analysis.expires_at,
            )
        return ConvertResponse(
            processing_id=analysis.analysis_id,
            quota_remaining=quota_remaining,
            quota_limit=identity.quota_limit,
            identity_type=identity.identity_type,
            analysis=analysis,
        )
    except FileTooLargeError:
        max_bytes = int(identity.max_upload_size_bytes) if identity is not None else 2 * 1024 * 1024
        max_mb = max(1, int(max_bytes // (1024 * 1024)))
        raise HTTPException(status_code=413, detail=f"File exceeds maximum size of {max_mb} MB.")
    except InvalidUserTokenError:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid identity context. Send anonymous_fingerprint or a valid user_token.",
        )
    except QuotaExceededError:
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
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")
    except AnalysisAccessDeniedError:
        raise HTTPException(status_code=403, detail="Access denied for this analysis.")
