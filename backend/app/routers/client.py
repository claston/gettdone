from fastapi import APIRouter, Depends, HTTPException, Query

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service
from app.schemas import ClientConversionItem, ClientConversionsResponse

router = APIRouter()


@router.get("/client/conversions", response_model=ClientConversionsResponse)
def get_client_conversions(
    user_token: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> ClientConversionsResponse:
    try:
        identity = access_control_service.resolve_identity(anonymous_fingerprint=None, user_token=user_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    if identity.identity_type != "user":
        raise HTTPException(status_code=403, detail="Client area is only available for registered users.")

    items = access_control_service.list_user_conversions(
        user_id=identity.identity_id,
        limit=limit,
    )
    return ClientConversionsResponse(items=[ClientConversionItem(**item) for item in items])
