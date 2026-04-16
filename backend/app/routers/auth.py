from fastapi import APIRouter, Depends, HTTPException

from app.application import AccessControlService, UserAlreadyExistsError
from app.dependencies import get_access_control_service
from app.schemas import RegisterRequest, RegisterResponse

router = APIRouter()


@router.post("/auth/register", response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    service: AccessControlService = Depends(get_access_control_service),
) -> RegisterResponse:
    try:
        user = service.register_user(name=payload.name, email=payload.email, password=payload.password)
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Email already registered.")

    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    return RegisterResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        user_token=user.token,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
    )
