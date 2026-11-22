from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.application import AccessControlService, InvalidUserTokenError
from app.application.office_plan import office_commercial
from app.dependencies import get_access_control_service
from app.routers.access_control_common import SESSION_ACCESS_COOKIE_NAME, require_admin_actor
from app.schemas import (
    AdminActivatePlanRequest,
    AdminActivatePlanResponse,
    PlanCatalogItem,
    PlanCatalogResponse,
)

router = APIRouter()


class OfficePlanConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    price_cents: int | None = Field(default=None, ge=1)
    quota_limit: int | None = Field(default=None, ge=1)
    max_pages_per_file: int = Field(default=100, ge=1, le=100)
    max_pages_per_file_ocr: int = Field(default=6, ge=1, le=100)
    batch_max_files: int = Field(default=12, ge=1, le=30)


class OfficePlanAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None


def office_admin(action, payload, access, x_admin_token, authorization, access_cookie_token):
    actor_kind, actor_user_id = require_admin_actor(access_control_service=access,
        x_admin_token=x_admin_token, authorization=authorization, access_cookie_token=access_cookie_token)
    values = payload.model_dump(mode='json', exclude={'operation_id'})
    if action == 'activate' and not values.get('expires_at'):
        raise HTTPException(400, 'Informe o fim do acesso contratado.')
    if action == 'configure' and values['max_pages_per_file_ocr'] > values['max_pages_per_file']:
        raise HTTPException(400, 'O limite de OCR não pode superar o limite de páginas por arquivo.')
    try:
        return office_commercial(access, actor_kind=actor_kind, actor_user_id=actor_user_id,
                                 operation_id=payload.operation_id, action=action, values=values)
    except (ValueError, InvalidUserTokenError) as exc:
        raise HTTPException(400, str(exc) or 'Confira o usuário e o plano informado.') from None


@router.post('/admin/plans/office/configure')
def configure_office(payload: OfficePlanConfiguration, x_admin_token: str | None = Header(None),
                     authorization: str | None = Header(None),
                     access_cookie_token: str | None = Cookie(None, alias=SESSION_ACCESS_COOKIE_NAME),
                     access: AccessControlService = Depends(get_access_control_service)):
    return office_admin('configure', payload, access, x_admin_token, authorization, access_cookie_token)


@router.post('/admin/plans/office/activate')
def activate_office(payload: OfficePlanAccess, x_admin_token: str | None = Header(None),
                    authorization: str | None = Header(None),
                    access_cookie_token: str | None = Cookie(None, alias=SESSION_ACCESS_COOKIE_NAME),
                    access: AccessControlService = Depends(get_access_control_service)):
    return office_admin('activate', payload, access, x_admin_token, authorization, access_cookie_token)


@router.post('/admin/plans/office/cancel')
def cancel_office(payload: OfficePlanAccess, x_admin_token: str | None = Header(None),
                  authorization: str | None = Header(None),
                  access_cookie_token: str | None = Cookie(None, alias=SESSION_ACCESS_COOKIE_NAME),
                  access: AccessControlService = Depends(get_access_control_service)):
    return office_admin('cancel', payload, access, x_admin_token, authorization, access_cookie_token)


@router.get("/plans", response_model=PlanCatalogResponse)
def list_plans(
    response: Response,
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> PlanCatalogResponse:
    items = access_control_service.list_public_plans()
    max_age = min(86400, max(0, access_control_service.public_plans_cache_ttl_seconds))
    response.headers["Cache-Control"] = f"public, max-age={max_age}, stale-while-revalidate=60"
    return PlanCatalogResponse(items=[PlanCatalogItem(**item) for item in items])

@router.post("/admin/plans/activate", response_model=AdminActivatePlanResponse)
def activate_user_plan(
    payload: AdminActivatePlanRequest,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminActivatePlanResponse:
    actor_kind, actor_user_id = require_admin_actor(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    if payload.plan_code.strip() == 'escritorio_documentos':
        raise HTTPException(400, 'Use a ativação do piloto Escritório com data de término e operação auditada.')

    try:
        activated = access_control_service.activate_user_plan(
            user_id=payload.user_id.strip(),
            plan_code=payload.plan_code.strip(),
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    except InvalidUserTokenError:
        raise HTTPException(status_code=404, detail="User not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AdminActivatePlanResponse(
        user_id=payload.user_id.strip(),
        plan_code=str(activated["code"]),
        plan_name=str(activated["name"]),
        plan_version=int(activated["version"]),
        quota_mode=str(activated["quota_mode"]),
        quota_limit=int(activated["quota_limit"]),
    )
