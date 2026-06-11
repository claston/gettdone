import logging
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
from app.routers.access_control_common import (
    SESSION_ACCESS_COOKIE_NAME,
    SESSION_REFRESH_COOKIE_NAME,
    clear_session_cookies,
    resolve_header_query_or_cookie_token,
    set_session_cookies,
)
from app.schemas import (
    AuthMeResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    SessionAuthResponse,
    SessionLogoutResponse,
)
from app.security_baseline import is_production_env

router = APIRouter()
logger = logging.getLogger(__name__)


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
    resolved_token = resolve_header_query_or_cookie_token(
        authorization=authorization,
        query_token=user_token,
        cookie_token=None,
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
    set_session_cookies(
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
    set_session_cookies(
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
    clear_session_cookies(response)
    return response


@router.post("/auth/session/logout-all", response_model=SessionLogoutResponse)
def session_logout_all(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    resolved_token = resolve_header_query_or_cookie_token(
        authorization=authorization,
        query_token=user_token,
        cookie_token=None,
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
        clear_session_cookies(response)
        return response
    try:
        user = service.get_user_by_token(user_token=resolved_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    service.revoke_all_user_sessions(user_id=user.user_id)
    response = JSONResponse(content=SessionLogoutResponse().model_dump())
    response.headers["Cache-Control"] = "no-store"
    clear_session_cookies(response)
    return response


@router.get("/auth/google/start")
def google_start(
    next_path: str = Query(default="/client-area.html", alias="next"),
    flow: str = Query(default="login"),
    accepted_terms: bool = Query(default=False),
    product_updates_opt_in: bool = Query(default=False),
    oauth_service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> RedirectResponse:
    normalized_flow = "signup" if str(flow or "").strip().lower() == "signup" else "login"
    if normalized_flow == "signup" and not accepted_terms:
        raise HTTPException(
            status_code=400,
            detail="Você precisa aceitar os Termos de Uso e a Política de Privacidade para criar sua conta com Google.",
        )
    try:
        auth_url = oauth_service.build_authorization_url(
            next_path=next_path,
            flow_mode=normalized_flow,
            terms_accepted=accepted_terms,
            product_updates_opt_in=product_updates_opt_in,
        )
    except GoogleOAuthNotConfiguredError:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    logger.info(
        "google_oauth_start next=%s redirect_uri=%s",
        next_path,
        getattr(oauth_service.config, "redirect_uri", ""),
    )
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
    except (GoogleOAuthStateError, GoogleOAuthExchangeError) as exc:
        logger.exception(
            "google_oauth_callback_failed state=%s redirect_uri=%s frontend_base_url=%s error=%s",
            state,
            getattr(oauth_service.config, "redirect_uri", ""),
            getattr(oauth_service.config, "frontend_base_url", ""),
            exc,
        )
        payload = {
            "error": "google_oauth_failed",
            "next": "/client-area.html",
        }
        if not is_production_env():
            payload["error_detail"] = str(exc).strip() or exc.__class__.__name__
        params = urlencode(payload)
        fallback = f"{oauth_service.config.frontend_base_url}/auth-callback.html?{params}"
        return RedirectResponse(url=fallback, status_code=307)
    logger.info("google_oauth_callback_succeeded")
    return RedirectResponse(url=redirect_url, status_code=307)
