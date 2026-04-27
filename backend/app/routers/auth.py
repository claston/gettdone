from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.application import (
    AccessControlService,
    GoogleOAuthExchangeError,
    GoogleOAuthNotConfiguredError,
    GoogleOAuthService,
    GoogleOAuthStateError,
    InvalidCredentialsError,
    InvalidUserTokenError,
    UserAlreadyExistsError,
)
from app.dependencies import get_access_control_service, get_google_oauth_service
from app.schemas import AuthMeResponse, LoginRequest, LoginResponse, RegisterRequest, RegisterResponse

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


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    service: AccessControlService = Depends(get_access_control_service),
) -> LoginResponse:
    try:
        user = service.authenticate_user(email=payload.email, password=payload.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    return LoginResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        user_token=user.token,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
    )


@router.get("/auth/me", response_model=AuthMeResponse)
def me(
    user_token: str,
    service: AccessControlService = Depends(get_access_control_service),
) -> AuthMeResponse:
    try:
        user = service.get_user_by_token(user_token=user_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user_token)
    return AuthMeResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
    )


@router.get("/auth/google/start")
def google_start(
    next_path: str = Query(default="/client-area.html", alias="next"),
    oauth_service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> RedirectResponse:
    try:
        auth_url = oauth_service.build_authorization_url(next_path=next_path)
    except GoogleOAuthNotConfiguredError:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    return RedirectResponse(url=auth_url, status_code=307)


@router.get("/auth/google/callback")
def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> RedirectResponse:
    try:
        redirect_url = oauth_service.build_callback_redirect_url(code=code, state=state)
    except GoogleOAuthNotConfiguredError:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    except (GoogleOAuthStateError, GoogleOAuthExchangeError):
        params = urlencode(
            {
                "error": "google_oauth_failed",
                "next": "/client-area.html",
            }
        )
        fallback = f"{oauth_service.config.frontend_base_url}/auth-callback.html?{params}"
        return RedirectResponse(url=fallback, status_code=307)
    return RedirectResponse(url=redirect_url, status_code=307)
