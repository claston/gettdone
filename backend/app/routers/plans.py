from fastapi import APIRouter, Cookie, Depends, Header, HTTPException

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.routers.access_control_common import SESSION_ACCESS_COOKIE_NAME, require_admin_actor
from app.schemas import (
    AdminActivatePlanRequest,
    AdminActivatePlanResponse,
    PlanCatalogItem,
    PlanCatalogResponse,
)

router = APIRouter()


@router.get("/plans", response_model=PlanCatalogResponse)
def list_plans(
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> PlanCatalogResponse:
    items = access_control_service.list_public_plans()
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
