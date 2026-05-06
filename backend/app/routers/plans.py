import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.schemas import (
    AdminActivatePlanRequest,
    AdminActivatePlanResponse,
    PlanCatalogItem,
    PlanCatalogResponse,
)

router = APIRouter()


def _resolve_admin_token(
    *,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
) -> str:
    if x_admin_token and x_admin_token.strip():
        return x_admin_token.strip()
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    return (admin_token_query or "").strip()


@router.get("/plans", response_model=PlanCatalogResponse)
def list_plans(
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> PlanCatalogResponse:
    items = access_control_service.list_public_plans()
    return PlanCatalogResponse(items=[PlanCatalogItem(**item) for item in items])


@router.post("/admin/plans/activate", response_model=AdminActivatePlanResponse)
@router.post("/plans/activate", response_model=AdminActivatePlanResponse, include_in_schema=False)
def activate_user_plan(
    payload: AdminActivatePlanRequest,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None, alias="admin_token"),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminActivatePlanResponse:
    provided_admin_token = _resolve_admin_token(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
    )
    if not provided_admin_token:
        raise HTTPException(status_code=401, detail="Admin token is required.")
    expected_admin_token = os.getenv("PLANS_ADMIN_TOKEN", "").strip()
    actor_kind = "legacy_token"
    actor_user_id: str | None = None
    if expected_admin_token and secrets.compare_digest(provided_admin_token, expected_admin_token):
        pass
    else:
        try:
            admin_user = access_control_service.get_user_by_token(user_token=provided_admin_token)
        except InvalidUserTokenError:
            raise HTTPException(status_code=401, detail="Invalid admin token.")
        if not access_control_service.is_user_admin(user_id=admin_user.user_id):
            raise HTTPException(status_code=403, detail="Admin access required.")
        actor_kind = "admin_user"
        actor_user_id = admin_user.user_id

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
