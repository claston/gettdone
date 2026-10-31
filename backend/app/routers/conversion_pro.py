"""Explicit paid OCR capabilities and preflight; no cloud OCR during estimation."""

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response

from app.application.conversion.pro_conversion import ConversionProAccessError, ConversionProQuota, inspect_pro_pdf
from app.application.errors import QuotaExceededError
from app.dependencies import get_access_control_service, get_conversion_batch_document_store, get_conversion_batch_service
from app.routers.auth_session import ANONYMOUS_IDENTITY_COOKIE_NAME, SESSION_ACCESS_COOKIE_NAME
from app.routers.conversion_batches import _resolve_optional_identity

router = APIRouter()


def pro_identity(
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    access=Depends(get_access_control_service),
):
    identity = _resolve_optional_identity(
        access_control_service=access,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
        anonymous_cookie_token=anonymous_cookie_token,
    )
    return access, identity


@router.get("/api/pro/conversion-options")
def conversion_pro_options(response: Response, context=Depends(pro_identity)):
    access, identity = context
    budget = ConversionProQuota(access)
    eligible = bool(identity and budget.eligible(identity))
    response.headers["Cache-Control"] = "no-store"
    return {
        "enabled": budget.policy.enabled,
        "eligible": eligible,
        "monthly_pages": budget.policy.monthly_pages,
        "remaining_pages": budget.remaining(identity) if eligible else 0,
        "max_pages": min(budget.policy.max_pages, getattr(identity, "max_pages_per_file_ocr", None) or budget.policy.max_pages),
        "reset_at": budget.reset_at(),
    }


@router.get("/api/pro/conversion-batches/{batch_id}/pro-estimate")
def estimate_conversion_pro(
    batch_id: str,
    response: Response,
    context=Depends(pro_identity),
    service=Depends(get_conversion_batch_service),
    store=Depends(get_conversion_batch_document_store),
):
    access, identity = context
    budget = ConversionProQuota(access)
    try:
        if identity is None:
            raise ConversionProAccessError("Entre em sua conta para usar a Conversão PRO.")
        budget.require_access(identity)
    except ConversionProAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if service is None or store is None:
        raise HTTPException(status_code=503, detail="A Conversão PRO está indisponível neste ambiente.")
    snapshot = service.repository.get_for_owner(batch_id, identity_type=identity.identity_type, identity_id=identity.identity_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Conversão não encontrada.")
    if len(snapshot.jobs) != 1 or snapshot.jobs[0].job.ocr_mode != "pro":
        raise HTTPException(status_code=400, detail="Esta solicitação não é uma Conversão PRO individual.")
    job = snapshot.jobs[0].job
    try:
        service.direct_upload_service.verify_uploaded(job.document)
        document = store.load(job.document)
        pages = inspect_pro_pdf(
            document.raw_bytes, max_pages=min(budget.policy.max_pages, identity.max_pages_per_file_ocr or budget.policy.max_pages)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=str(exc) if isinstance(exc, ValueError) else "Não foi possível conferir o PDF enviado."
        ) from exc
    remaining = budget.remaining(identity)
    if pages > remaining:
        raise HTTPException(status_code=429, detail="O limite disponível da Conversão PRO é insuficiente para este PDF.")
    try:
        access.ensure_quota_available(identity, required_units=pages if identity.quota_mode == "pages" else 1)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail="O saldo de páginas do plano é insuficiente para este PDF.") from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "batch_id": batch_id,
        "pages": pages,
        "remaining_pages": remaining,
        "remaining_after": remaining - pages,
        "monthly_pages": budget.policy.monthly_pages,
        "reset_at": budget.reset_at(),
    }
