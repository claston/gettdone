import os
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.application import (
    AccessControlService,
    GoogleOAuthExchangeError,
    GoogleOAuthNotConfiguredError,
    GoogleOAuthService,
    GoogleOAuthStateError,
    InvalidCredentialsError,
    InvalidSessionTokenError,
    InvalidUserTokenError,
    ReusedSessionTokenError,
    UserAlreadyExistsError,
)
from app.dependencies import get_access_control_service, get_google_oauth_service
from app.schemas import (
    AuthMeResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    SessionAuthResponse,
    SessionLogoutResponse,
)
from app.security_baseline import is_production_env, read_bool_env

router = APIRouter()
SESSION_ACCESS_COOKIE_NAME = os.getenv("SESSION_ACCESS_COOKIE_NAME", "__Host-ofx_at").strip() or "__Host-ofx_at"
SESSION_REFRESH_COOKIE_NAME = os.getenv("SESSION_REFRESH_COOKIE_NAME", "__Secure-ofx_rt").strip() or "__Secure-ofx_rt"
SESSION_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_ACCESS_TOKEN_TTL_SECONDS", "900"))
SESSION_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_REFRESH_TOKEN_TTL_SECONDS", "1209600"))
SESSION_COOKIE_SECURE = read_bool_env("SESSION_COOKIE_SECURE", default=is_production_env())


def _resolve_user_token(
    *,
    authorization: str | None,
    user_token_query: str | None,
    access_cookie_token: str | None = None,
) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    cookie_token = (access_cookie_token or "").strip()
    if cookie_token:
        return cookie_token
    return (user_token_query or "").strip()


def _auth_me_payload(*, service: AccessControlService, user_token: str, response_model=AuthMeResponse):
    user = service.get_user_by_token(user_token=user_token)
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user_token)
    return response_model(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
    )


def _set_session_cookies(response: JSONResponse, *, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key=SESSION_ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=SESSION_ACCESS_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=SESSION_REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=SESSION_REFRESH_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/auth/session/refresh",
    )


def _clear_session_cookies(response: JSONResponse) -> None:
    response.delete_cookie(
        key=SESSION_ACCESS_COOKIE_NAME,
        path="/",
        secure=SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=SESSION_REFRESH_COOKIE_NAME,
        path="/auth/session/refresh",
        secure=SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )


@router.post("/auth/register", response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    service: AccessControlService = Depends(get_access_control_service),
) -> RegisterResponse:
    if not payload.accepted_terms:
        raise HTTPException(
            status_code=400,
            detail="Você precisa aceitar os Termos de Uso e a Política de Privacidade para criar a conta.",
        )

    accepted_at = service.now_provider().isoformat()
    product_updates_opted_in_at = accepted_at if payload.product_updates_opt_in else None
    try:
        user = service.register_user(
            name=payload.name,
            email=payload.email,
            password=payload.password,
            terms_accepted_at=accepted_at,
            privacy_accepted_at=accepted_at,
            product_updates_opt_in=payload.product_updates_opt_in,
            product_updates_opted_in_at=product_updates_opted_in_at,
        )
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
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
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
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
    )


@router.get("/auth/me", response_model=AuthMeResponse)
def me(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> AuthMeResponse:
    resolved_token = _resolve_user_token(
        authorization=authorization,
        user_token_query=user_token,
        access_cookie_token=None,
    )
    if resolved_token:
        try:
            return _auth_me_payload(service=service, user_token=resolved_token)
        except InvalidUserTokenError:
            raise HTTPException(status_code=401, detail="Invalid user token.")
    access_cookie = (access_cookie_token or "").strip()
    if not access_cookie:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    try:
        user = service.get_user_by_session_access_token(access_cookie)
    except InvalidSessionTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    return AuthMeResponse(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
    )


@router.post("/auth/session/login", response_model=SessionAuthResponse)
def session_login(
    payload: LoginRequest,
    request: Request,
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    try:
        user = service.authenticate_user(email=payload.email, password=payload.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    session_bundle = service.create_user_session(
        user_id=user.user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=session_bundle.user.token)
    payload_model = SessionAuthResponse(
        user_id=session_bundle.user.user_id,
        name=session_bundle.user.name,
        email=session_bundle.user.email,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
        access_expires_at=session_bundle.access_expires_at,
        refresh_expires_at=session_bundle.refresh_expires_at,
    )
    response = JSONResponse(content=payload_model.model_dump())
    response.headers["Cache-Control"] = "no-store"
    _set_session_cookies(
        response,
        access_token=session_bundle.access_token,
        refresh_token=session_bundle.refresh_token,
    )
    return response


@router.post("/auth/session/refresh", response_model=SessionAuthResponse)
def session_refresh(
    request: Request,
    refresh_cookie_token: str | None = Cookie(default=None, alias=SESSION_REFRESH_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    refresh_token = (refresh_cookie_token or "").strip()
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh session token.")
    try:
        session_bundle = service.refresh_user_session(
            refresh_token=refresh_token,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ReusedSessionTokenError:
        raise HTTPException(status_code=401, detail="Session token reuse detected. Please login again.")
    except InvalidSessionTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh session token.")
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=session_bundle.user.token)
    payload_model = SessionAuthResponse(
        user_id=session_bundle.user.user_id,
        name=session_bundle.user.name,
        email=session_bundle.user.email,
        quota_remaining=service.get_remaining_quota(identity),
        quota_limit=identity.quota_limit,
        quota_mode=identity.quota_mode,
        plan_code=identity.plan_code,
        plan_name=identity.plan_name,
        max_upload_size_bytes=identity.max_upload_size_bytes,
        max_pages_per_file=identity.max_pages_per_file,
        access_expires_at=session_bundle.access_expires_at,
        refresh_expires_at=session_bundle.refresh_expires_at,
    )
    response = JSONResponse(content=payload_model.model_dump())
    response.headers["Cache-Control"] = "no-store"
    _set_session_cookies(
        response,
        access_token=session_bundle.access_token,
        refresh_token=session_bundle.refresh_token,
    )
    return response


@router.post("/auth/session/logout", response_model=SessionLogoutResponse)
def session_logout(
    refresh_cookie_token: str | None = Cookie(default=None, alias=SESSION_REFRESH_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    refresh_token = (refresh_cookie_token or "").strip()
    if refresh_token:
        service.revoke_user_session(refresh_token=refresh_token)
    response = JSONResponse(content=SessionLogoutResponse().model_dump())
    response.headers["Cache-Control"] = "no-store"
    _clear_session_cookies(response)
    return response


@router.post("/auth/session/logout-all", response_model=SessionLogoutResponse)
def session_logout_all(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    resolved_token = _resolve_user_token(
        authorization=authorization,
        user_token_query=user_token,
        access_cookie_token=None,
    )
    if not resolved_token:
        access_cookie = (access_cookie_token or "").strip()
        if not access_cookie:
            raise HTTPException(status_code=401, detail="Invalid user token.")
        try:
            user = service.get_user_by_session_access_token(access_cookie)
        except InvalidSessionTokenError:
            raise HTTPException(status_code=401, detail="Invalid user token.")
        service.revoke_all_user_sessions(user_id=user.user_id)
        response = JSONResponse(content=SessionLogoutResponse().model_dump())
        response.headers["Cache-Control"] = "no-store"
        _clear_session_cookies(response)
        return response
    try:
        user = service.get_user_by_token(user_token=resolved_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    service.revoke_all_user_sessions(user_id=user.user_id)
    response = JSONResponse(content=SessionLogoutResponse().model_dump())
    response.headers["Cache-Control"] = "no-store"
    _clear_session_cookies(response)
    return response


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
