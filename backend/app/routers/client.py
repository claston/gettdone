from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.routers.auth_session import SESSION_ACCESS_COOKIE_NAME, resolve_user_token_with_session
from app.schemas import ClientConversionItem, ClientConversionsResponse

router = APIRouter()


@router.get("/client/conversions", response_model=ClientConversionsResponse)
def get_client_conversions(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    limit: int = Query(default=20, ge=1, le=100),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ClientConversionsResponse:
    resolved_token = resolve_user_token_with_session(
        access_control_service=access_control_service,
        authorization=authorization,
        explicit_user_token=user_token,
        access_cookie_token=access_cookie_token,
    )
    if not resolved_token:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    try:
        user = access_control_service.get_user_by_token(user_token=resolved_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    items = access_control_service.list_user_conversions(
        user_id=user.user_id,
        limit=limit,
    )
    return ClientConversionsResponse(items=[ClientConversionItem(**item) for item in items])
