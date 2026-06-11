from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.application import AccessControlService, InvalidCredentialsError, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.routers.access_control_common import require_admin_user
from app.schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminMeResponse,
    AdminSetUserRoleRequest,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserRoleEventItem,
    AdminUserRoleHistoryResponse,
)

router = APIRouter()


@router.post("/admin/auth/login", response_model=AdminLoginResponse)
def admin_login(
    payload: AdminLoginRequest,
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminLoginResponse:
    try:
        user = access_control_service.authenticate_user(email=payload.email, password=payload.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not access_control_service.is_user_admin(user_id=user.user_id):
        raise HTTPException(status_code=403, detail="Admin access required.")

    return AdminLoginResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        admin_token=user.token,
    )


@router.get("/admin/me", response_model=AdminMeResponse)
def admin_me(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminMeResponse:
    user = require_admin_user(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
        access_control_service=access_control_service,
    )

    return AdminMeResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
    )


@router.post("/admin/auth/logout")
def admin_logout() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/admin/users", response_model=AdminUserListResponse)
def list_users_for_admin(
    query: str = Query(default=""),
    only_admin: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminUserListResponse:
    require_admin_user(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
        access_control_service=access_control_service,
    )
    items, total = access_control_service.list_users_for_admin(
        query=query,
        only_admin=only_admin,
        limit=limit,
        offset=offset,
    )
    return AdminUserListResponse(
        items=[AdminUserItem(**item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/admin/users/role", response_model=AdminUserItem)
def set_user_role_for_admin(
    payload: AdminSetUserRoleRequest,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminUserItem:
    actor_user = require_admin_user(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
        access_control_service=access_control_service,
    )
    target_user_id = payload.user_id.strip()
    if not target_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    if actor_user.user_id == target_user_id and not payload.is_admin:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access.")
    try:
        updated = access_control_service.set_user_admin_role_with_actor(
            user_id=target_user_id,
            is_admin=payload.is_admin,
            actor_user_id=actor_user.user_id,
        )
    except InvalidUserTokenError:
        raise HTTPException(status_code=404, detail="User not found.")
    return AdminUserItem(**updated)


@router.get("/admin/users/{user_id}/history", response_model=AdminUserRoleHistoryResponse)
def list_user_role_history_for_admin(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminUserRoleHistoryResponse:
    require_admin_user(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
        access_control_service=access_control_service,
    )
    clean_user_id = user_id.strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    items = access_control_service.list_user_role_events_for_admin(
        user_id=clean_user_id,
        limit=limit,
    )
    return AdminUserRoleHistoryResponse(
        user_id=clean_user_id,
        items=[AdminUserRoleEventItem(**item) for item in items],
    )
