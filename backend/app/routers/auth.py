import logging
import os
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.application import (
    AccessControlService,
    ContactDeliveryError,
    ContactProviderNotConfiguredError,
    ContactService,
    EmailVerificationRateLimitedError,
    EmailVerificationRequiredError,
    GoogleOAuthExchangeError,
    GoogleOAuthNotConfiguredError,
    GoogleOAuthService,
    GoogleOAuthStateError,
    InvalidCredentialsError,
    InvalidEmailVerificationTokenError,
    InvalidSessionTokenError,
    InvalidUserTokenError,
    ReusedSessionTokenError,
    UserAlreadyExistsError,
)
from app.application.access_control import DEFAULT_MAX_PAGES_PER_FILE, MAX_UPLOAD_SIZE_BYTES, SessionTokenBundle
from app.application.login_tracking import record_successful_login_safely
from app.application.marketing_consent import CURRENT_PRODUCT_UPDATES_CONSENT_VERSION
from app.dependencies import get_access_control_service, get_contact_service, get_google_oauth_service
from app.routers.access_control_common import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    SESSION_REFRESH_COOKIE_NAME,
    clear_session_cookies,
    resolve_header_query_or_cookie_token,
    set_anonymous_identity_cookie,
    set_session_cookies,
)
from app.schemas import (
    AnonymousSessionRequest,
    AnonymousSessionResponse,
    AuthMeResponse,
    EmailVerificationConfirmRequest,
    EmailVerificationConfirmResponse,
    EmailVerificationResendRequest,
    EmailVerificationResendResponse,
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
_LEGACY_ANONYMOUS_FINGERPRINT_PATTERN = re.compile(r"^anon-\d{10,16}-[0-9a-f]{4,32}$")


def _session_auth_response(*, service: AccessControlService, session_bundle: SessionTokenBundle) -> JSONResponse:
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


@router.post("/auth/anonymous-session", response_model=AnonymousSessionResponse)
def anonymous_session(
    payload: AnonymousSessionRequest,
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    cookie_token = str(anonymous_cookie_token or "").strip()
    if cookie_token:
        try:
            service.decode_anonymous_identity_token(token=cookie_token)
        except InvalidUserTokenError:
            pass
        else:
            response_model = AnonymousSessionResponse()
            response = JSONResponse(content=response_model.model_dump())
            response.headers["Cache-Control"] = "no-store"
            return response

    legacy_fingerprint = str(payload.legacy_fingerprint or "").strip()
    legacy_identity_id = None
    if _LEGACY_ANONYMOUS_FINGERPRINT_PATTERN.fullmatch(legacy_fingerprint):
        legacy_identity_id = service.get_anonymous_identity_id(fingerprint=legacy_fingerprint)

    if legacy_identity_id is not None:
        anonymous_token = service.issue_anonymous_identity_token(identity_id=legacy_identity_id)
        logger.info("anonymous_identity_legacy_migrated")
    else:
        fingerprint = service.generate_anonymous_fingerprint()
        anonymous_token = service.issue_anonymous_identity_token(fingerprint=fingerprint)
    response_model = AnonymousSessionResponse()
    response = JSONResponse(content=response_model.model_dump())
    response.headers["Cache-Control"] = "no-store"
    set_anonymous_identity_cookie(
        response,
        token=anonymous_token,
    )
    return response


def _is_basic_email_valid(value: str) -> bool:
    if not value or len(value) > 320 or any(character.isspace() for character in value):
        return False

    local_part, separator, domain = value.partition("@")
    if not local_part or not separator or not domain or "@" in domain:
        return False

    domain_name, dot, top_level_domain = domain.rpartition(".")
    return bool(domain_name and dot and top_level_domain)


def _email_verification_frontend_url() -> str:
    configured = os.getenv("AUTH_EMAIL_VERIFICATION_FRONTEND_URL", "").strip()
    return configured or "https://www.ofxsimples.com.br/verificar-email.html"


def _build_verification_email_text(*, name: str, token: str, expires_at: str) -> str:
    confirmation_url = f"{_email_verification_frontend_url()}#token={token}"
    display_name = str(name or "").strip() or "Olá"
    return "\n".join(
        [
            f"{display_name},",
            "",
            "Confirme seu e-mail para liberar sua conta no OFX Simples.",
            f"Confirme seu e-mail: {confirmation_url}",
            "",
            f"Este link expira em: {expires_at}.",
            "Se você não solicitou este cadastro, ignore esta mensagem.",
        ]
    )


async def _deliver_verification_email(
    *,
    service: AccessControlService,
    contact_service: ContactService,
    issued_token,
) -> str:
    try:
        delivery = await contact_service.send_text_email(
            to_email=issued_token.email,
            subject="Confirme seu e-mail no OFX Simples",
            text=_build_verification_email_text(
                name=issued_token.name,
                token=issued_token.token,
                expires_at=issued_token.expires_at,
            ),
        )
    except (ContactProviderNotConfiguredError, ContactDeliveryError):
        service.mark_email_verification_delivery(token_id=issued_token.token_id, status="failed")
        logger.exception(
            "email_verification_delivery_failed user_id=%s token_id=%s",
            issued_token.user_id,
            issued_token.token_id,
        )
        return "failed"

    service.mark_email_verification_delivery(
        token_id=issued_token.token_id,
        status="sent",
        provider_message_id=delivery.provider_message_id,
    )
    logger.info(
        "email_verification_delivery_succeeded user_id=%s token_id=%s mode=%s",
        issued_token.user_id,
        issued_token.token_id,
        delivery.delivery_mode,
    )
    return "sent"


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
async def register(
    payload: RegisterRequest,
    service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> RegisterResponse:
    if not payload.accepted_terms:
        raise HTTPException(
            status_code=400,
            detail="Você precisa aceitar os Termos de Uso e a Política de Privacidade para criar a conta.",
        )

    normalized_email = payload.email.strip().lower()
    if not _is_basic_email_valid(normalized_email):
        raise HTTPException(status_code=400, detail="Informe um endereço de e-mail válido.")
    if not payload.name.strip() or len(payload.name.strip()) > 120:
        raise HTTPException(status_code=400, detail="Informe um nome válido.")
    if len(payload.password) < 8 or len(payload.password) > 256:
        raise HTTPException(status_code=400, detail="A senha deve ter entre 8 e 256 caracteres.")

    accepted_at = service.now_provider().isoformat()
    product_updates_opted_in_at = accepted_at if payload.product_updates_opt_in else None
    try:
        user = service.register_user(
            name=payload.name,
            email=normalized_email,
            password=payload.password,
            terms_accepted_at=accepted_at,
            privacy_accepted_at=accepted_at,
            product_updates_opt_in=payload.product_updates_opt_in,
            product_updates_opted_in_at=product_updates_opted_in_at,
            product_updates_consent_version=(
                CURRENT_PRODUCT_UPDATES_CONSENT_VERSION
                if payload.product_updates_opt_in
                else None
            ),
        )
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Email already registered.")

    if user.email_verification_status == "pending":
        issued_token = service.issue_email_verification_token(user_id=user.user_id)
        delivery_status = await _deliver_verification_email(
            service=service,
            contact_service=contact_service,
            issued_token=issued_token,
        )
        return RegisterResponse(
            user_id=user.user_id,
            name=user.name,
            email=user.email,
            user_token=None,
            quota_remaining=0,
            quota_limit=service.registered_quota_limit,
            quota_mode="conversion",
            max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
            max_pages_per_file=DEFAULT_MAX_PAGES_PER_FILE,
            verification_required=True,
            verification_status="pending",
            email_delivery_status=delivery_status,
        )

    record_successful_login_safely(service, user_id=user.user_id, auth_method="local_password")

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
        verification_required=False,
        verification_status="verified",
    )


@router.post("/auth/email-verification/confirm", response_model=EmailVerificationConfirmResponse)
def confirm_email_verification(
    payload: EmailVerificationConfirmRequest,
    service: AccessControlService = Depends(get_access_control_service),
) -> EmailVerificationConfirmResponse:
    try:
        service.confirm_email_verification_token(token=payload.token)
    except InvalidEmailVerificationTokenError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_or_expired_email_verification_token",
                "message": "O link de confirmação é inválido, expirou ou já foi utilizado.",
            },
        )
    return EmailVerificationConfirmResponse()


@router.post(
    "/auth/email-verification/resend",
    response_model=EmailVerificationResendResponse,
    status_code=202,
)
async def resend_email_verification(
    payload: EmailVerificationResendRequest,
    service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> EmailVerificationResendResponse:
    try:
        issued_token = service.issue_email_verification_token_for_email(email=payload.email)
    except EmailVerificationRateLimitedError:
        issued_token = None
    if issued_token is not None:
        await _deliver_verification_email(
            service=service,
            contact_service=contact_service,
            issued_token=issued_token,
        )
    return EmailVerificationResendResponse(
        message="Se existir uma conta pendente para este e-mail, enviaremos uma nova mensagem.",
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    service: AccessControlService = Depends(get_access_control_service),
) -> LoginResponse:
    try:
        user = service.authenticate_user(email=payload.email, password=payload.password)
    except EmailVerificationRequiredError:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_verification_required",
                "message": "Confirme seu e-mail para acessar sua conta.",
            },
        )
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    record_successful_login_safely(
        service,
        user_id=user.user_id,
        auth_method="local_password",
    )

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
    except EmailVerificationRequiredError:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_verification_required",
                "message": "Confirme seu e-mail para acessar sua conta.",
            },
        )
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    session_bundle = service.create_user_session(
        user_id=user.user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    record_successful_login_safely(
        service,
        user_id=user.user_id,
        auth_method="local_password",
    )
    return _session_auth_response(service=service, session_bundle=session_bundle)


@router.post("/auth/session/migrate", response_model=SessionAuthResponse)
def session_migrate(
    request: Request,
    authorization: str | None = Header(default=None),
    service: AccessControlService = Depends(get_access_control_service),
) -> JSONResponse:
    legacy_token = resolve_header_query_or_cookie_token(
        authorization=authorization,
        query_token=None,
        cookie_token=None,
    )
    if not legacy_token:
        raise HTTPException(status_code=401, detail="Legacy user token is required.")
    try:
        user = service.get_user_by_token(user_token=legacy_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    session_bundle = service.create_user_session(
        user_id=user.user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return _session_auth_response(service=service, session_bundle=session_bundle)


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
    return _session_auth_response(service=service, session_bundle=session_bundle)


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
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    oauth_service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> RedirectResponse:
    try:
        callback_result = oauth_service.complete_callback(code=code, state=state)
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
        response = RedirectResponse(url=fallback, status_code=307)
        response.headers["Cache-Control"] = "no-store"
        return response
    logger.info("google_oauth_callback_succeeded")
    response = RedirectResponse(url=callback_result.redirect_url, status_code=307)
    response.headers["Cache-Control"] = "no-store"
    if callback_result.user is not None:
        session_bundle = oauth_service.access_control_service.create_user_session(
            user_id=callback_result.user.user_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        set_session_cookies(
            response,
            access_token=session_bundle.access_token,
            refresh_token=session_bundle.refresh_token,
        )
    return response
