from __future__ import annotations

import base64
import binascii
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.application import AccessControlService, InvalidUserTokenError
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.organization_suggestions import build_source_evidence, suggest_organization
from app.application.pro_organization import OrganizationConflict, OrganizationService
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME, resolve_user_token_with_session
from app.routers.pro_security import require_pro_browser_request
from app.security_baseline import is_production_env, read_bool_env

router = APIRouter(prefix="/api/pro/organization", tags=["PRO organization"])


def conversion_owner(
    response: Response,
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access: AccessControlService = Depends(get_access_control_service),
) -> str:
    token = resolve_user_token_with_session(
        access_control_service=access, authorization=authorization, explicit_user_token=None, access_cookie_token=access_cookie_token
    )
    if not token:
        raise HTTPException(401, "Entre na sua conta para organizar conversões.")
    try:
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
    except InvalidUserTokenError:
        raise HTTPException(401, "Entre novamente na sua conta.") from None
    if identity.identity_type != "user" or not identity.batch_conversion_enabled:
        raise HTTPException(403, "A organização de conversões exige acesso ativo ao PRO.")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return identity.identity_id


def organization_enabled() -> bool:
    return read_bool_env("PRO_ORGANIZATION_ENABLED", default=not is_production_env())


def organization_owner(owner: str = Depends(conversion_owner)) -> str:
    if not organization_enabled():
        raise HTTPException(503, "A organização de conversões ainda não está disponível. Seus arquivos continuam acessíveis.")
    return owner


def organization_service(access: AccessControlService = Depends(get_access_control_service)) -> OrganizationService:
    return OrganizationService(access.db)


class ClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    tax_id: str | None = Field(default=None, max_length=30)
    trade_name: str = Field(default="", max_length=160)


class AccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    client_id: str = Field(min_length=1, max_length=100)
    bank_code: str = Field(default="", max_length=20)
    branch: str = Field(default="", max_length=30)
    account_number: str = Field(default="", max_length=80)


class PeriodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    start_date: date
    end_date: date


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str | None = Field(default=None, max_length=100)
    account_id: str | None = Field(default=None, max_length=100)
    period_ids: list[str] = Field(default_factory=list, max_length=50)
    expected_version: int = Field(default=0, ge=0)


class BulkContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=100)
    context: ContextRequest


class BulkContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BulkContextItem] = Field(min_length=1, max_length=50)


@contextmanager
def organization_errors():
    try:
        yield
    except OrganizationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Registro não encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
def catalog(owner: str = Depends(organization_owner), service: OrganizationService = Depends(organization_service)) -> dict:
    return service.catalog(owner)


@router.post("/clients", status_code=201, dependencies=[Depends(require_pro_browser_request)])
def create_client(
    request: ClientRequest, owner: str = Depends(organization_owner), service: OrganizationService = Depends(organization_service)
) -> dict:
    with organization_errors():
        return service.save_client(owner, **request.model_dump())


@router.patch("/clients/{entity_id}", dependencies=[Depends(require_pro_browser_request)])
def edit_client(
    entity_id: str,
    request: ClientRequest,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
) -> dict:
    with organization_errors():
        return service.save_client(owner, entity_id=entity_id, **request.model_dump())


@router.post("/accounts", status_code=201, dependencies=[Depends(require_pro_browser_request)])
def create_account(
    request: AccountRequest, owner: str = Depends(organization_owner), service: OrganizationService = Depends(organization_service)
) -> dict:
    with organization_errors():
        return service.save_account(owner, **request.model_dump())


@router.patch("/accounts/{entity_id}", dependencies=[Depends(require_pro_browser_request)])
def edit_account(
    entity_id: str,
    request: AccountRequest,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
) -> dict:
    with organization_errors():
        return service.save_account(owner, entity_id=entity_id, **request.model_dump())


@router.post("/periods", status_code=201, dependencies=[Depends(require_pro_browser_request)])
def create_period(
    request: PeriodRequest, owner: str = Depends(organization_owner), service: OrganizationService = Depends(organization_service)
) -> dict:
    with organization_errors():
        return service.save_period(owner, **request.model_dump())


@router.patch("/periods/{entity_id}", dependencies=[Depends(require_pro_browser_request)])
def edit_period(
    entity_id: str,
    request: PeriodRequest,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
) -> dict:
    with organization_errors():
        return service.save_period(owner, entity_id=entity_id, **request.model_dump())


def owned_snapshot(service: ConversionBatchService | None, owner: str, batch_id: str):
    if service is None:
        raise HTTPException(503, "Não foi possível carregar as conversões agora. Tente novamente em instantes.")
    snapshot = service.repository.get_for_owner(batch_id, identity_type="user", identity_id=owner)
    if snapshot is None:
        raise HTTPException(404, "Conversão não encontrada.")
    return snapshot


@router.put("/batches/{batch_id}/jobs/{job_id}/context", dependencies=[Depends(require_pro_browser_request)])
def organize_conversion(
    batch_id: str,
    job_id: str,
    request: ContextRequest,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
) -> dict:
    snapshot = owned_snapshot(batches, owner, batch_id)
    if not any(record.job.job_id == job_id for record in snapshot.jobs):
        raise HTTPException(404, "Conversão não encontrada.")
    with organization_errors():
        return service.save_context(owner, job_id=job_id, **request.model_dump())


def decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        created, job_id = json.loads(raw)
        created_at = datetime.fromisoformat(created)
        if created_at.tzinfo is None or not re.fullmatch(r"job_[A-Za-z0-9_-]{1,80}", job_id):
            raise ValueError("Invalid cursor")
        return created_at.astimezone(timezone.utc), job_id
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(400, "Página de conversões inválida.") from None


@router.post("/contexts/bulk", dependencies=[Depends(require_pro_browser_request)])
def organize_bulk(
    request: BulkContextRequest,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
) -> dict:
    if len({item.job_id for item in request.items}) != len(request.items):
        raise HTTPException(400, "Selecione cada arquivo apenas uma vez.")
    snapshots = {}
    for item in request.items:
        if item.batch_id not in snapshots:
            snapshots[item.batch_id] = owned_snapshot(batches, owner, item.batch_id)
        if not any(record.job.job_id == item.job_id for record in snapshots[item.batch_id].jobs):
            raise HTTPException(404, "Conversão não encontrada.")
    results = []
    for item in request.items:
        try:
            with organization_errors():
                context = service.save_context(owner, job_id=item.job_id, **item.context.model_dump())
            results.append({"job_id": item.job_id, "status": 200, "context": context})
        except HTTPException as exc:
            results.append({"job_id": item.job_id, "status": exc.status_code, "error": exc.detail})
    return {"items": results}


@router.get("/batches/{batch_id}/jobs/{job_id}/suggestions")
def organization_suggestions(
    batch_id: str, job_id: str,
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
) -> dict:
    snapshot = owned_snapshot(batches, owner, batch_id)
    record = next((row for row in snapshot.jobs if row.job.job_id == job_id), None)
    if record is None:
        raise HTTPException(404, "Conversão não encontrada.")
    if record.expires_at <= datetime.now(timezone.utc) or record.status == "expired":
        raise HTTPException(410, "O arquivo expirou.")
    payload = record.result.payload if record.result else {}
    source = (payload or {}).get("organization_source")
    if not isinstance(source, dict):
        # Legacy results may already contain a destination account chosen for export.
        # Only use explicit holder/document metadata and dated movements in that case.
        source = build_source_evidence({key: value for key, value in ((payload or {}).get("analysis") or {}).items()
                                        if key in {"financial_document", "preview_transactions"}})
    return suggest_organization(source, service.catalog(owner))


@router.get("/conversions")
def list_conversions(
    owner: str = Depends(organization_owner),
    service: OrganizationService = Depends(organization_service),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=30, ge=1, le=50),
    client_id: str | None = Query(default=None, max_length=100),
    period_id: str | None = Query(default=None, max_length=100),
    account_id: str | None = Query(default=None, max_length=100),
    unassigned: bool = False,
) -> dict:
    return conversion_page(owner, batches, service, cursor, limit, client_id, period_id, account_id, unassigned)


def conversion_page(owner, batches, service, cursor, limit, client_id, period_id, account_id, unassigned) -> dict:
    if batches is None:
        raise HTTPException(503, "Não foi possível carregar as conversões agora. Tente novamente em instantes.")
    if unassigned and client_id:
        raise HTTPException(400, "Escolha um cliente ou o filtro Sem cliente.")
    before = decode_cursor(cursor)
    # Scan bounded metadata chunks until the *filtered* page and its lookahead are full.
    # Never load financial payloads, even when matches are sparse across many pages.
    matches = []
    while len(matches) <= limit:
        summaries = batches.repository.list_files_for_owner(identity_type="user", identity_id=owner, limit=100, before=before)
        contexts = service.contexts(owner, [record.job_id for record in summaries]) if service else {}
        for record in summaries:
            context = contexts.get(record.job_id, {"client_id": None, "account_id": None, "period_ids": [], "version": 0})
            if ((client_id and context["client_id"] != client_id) or (unassigned and context["client_id"])
                    or (period_id and period_id not in context["period_ids"])
                    or (account_id and context["account_id"] != account_id)):
                continue
            matches.append((record, context))
            if len(matches) > limit:
                break
        if len(summaries) < 100:
            break
        before = summaries[-1].created_at, summaries[-1].job_id
    items = []
    now = datetime.now(timezone.utc)
    for record, context in matches[:limit]:
        expired = record.expires_at <= now or record.status == "expired"
        items.append(
            {
                "job_id": record.job_id,
                "batch_id": record.batch_id,
                "filename": record.filename,
                "status": "expired" if expired else record.status,
                "is_expired": expired,
                "created_at": record.created_at.isoformat(),
                "context": context,
                "movement_start_date": None,
                "movement_end_date": None,
                "review_url": None if expired else f"/review/{record.batch_id}/{record.job_id}",
            }
        )
    next_cursor = None
    if len(matches) > limit:
        last = matches[limit - 1][0]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.job_id]).encode()).decode().rstrip("=")
    return {"items": items, "next_cursor": next_cursor}
