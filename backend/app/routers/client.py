from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.schemas import ClientConversionItem, ClientConversionsResponse

router = APIRouter()


def _resolve_user_token(*, authorization: str | None, user_token_query: str | None) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    return (user_token_query or "").strip()


@router.get("/client/conversions", response_model=ClientConversionsResponse)
def get_client_conversions(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ClientConversionsResponse:
    resolved_token = _resolve_user_token(authorization=authorization, user_token_query=user_token)
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
