import os
import secrets

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.application import AccessControlService, InvalidSessionTokenError, InvalidUserTokenError
from app.security_baseline import is_production_env, read_bool_env

SESSION_ACCESS_COOKIE_NAME = os.getenv("SESSION_ACCESS_COOKIE_NAME", "__Host-ofx_at").strip() or "__Host-ofx_at"
SESSION_REFRESH_COOKIE_NAME = os.getenv("SESSION_REFRESH_COOKIE_NAME", "__Secure-ofx_rt").strip() or "__Secure-ofx_rt"
SESSION_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_ACCESS_TOKEN_TTL_SECONDS", "900"))
SESSION_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_REFRESH_TOKEN_TTL_SECONDS", "1209600"))
SESSION_COOKIE_SECURE = read_bool_env("SESSION_COOKIE_SECURE", default=is_production_env())


def resolve_header_query_or_cookie_token(
    *,
    authorization: str | None,
    query_token: str | None,
    cookie_token: str | None = None,
) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer

    resolved_cookie_token = (cookie_token or "").strip()
    if resolved_cookie_token:
        return resolved_cookie_token

    return (query_token or "").strip()


def resolve_user_token_with_session(
    *,
    access_control_service: AccessControlService,
    authorization: str | None,
    explicit_user_token: str | None,
    access_cookie_token: str | None,
) -> str:
    resolved_token = resolve_header_query_or_cookie_token(
        authorization=authorization,
        query_token=explicit_user_token,
        cookie_token=None,
    )
    if resolved_token:
        return resolved_token

    cookie_token = (access_cookie_token or "").strip()
    if not cookie_token:
        return ""
    try:
        user = access_control_service.get_user_by_session_access_token(cookie_token)
        return user.token
    except InvalidSessionTokenError:
        raise InvalidUserTokenError from None


def set_session_cookies(response: JSONResponse, *, access_token: str, refresh_token: str) -> None:
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


def clear_session_cookies(response: JSONResponse) -> None:
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


def resolve_admin_token(
    *,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
) -> str:
    if x_admin_token and x_admin_token.strip():
        return x_admin_token.strip()
    return resolve_header_query_or_cookie_token(
        authorization=authorization,
        query_token=admin_token_query,
        cookie_token=None,
    )


def require_admin_user(
    *,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
    access_control_service: AccessControlService,
):
    resolved_token = resolve_admin_token(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token_query,
    )
    if not resolved_token:
        raise HTTPException(status_code=401, detail="Admin token is required.")
    try:
        user = access_control_service.get_user_by_token(user_token=resolved_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid admin token.")
    if not access_control_service.is_user_admin(user_id=user.user_id):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def require_admin_actor(
    *,
    access_control_service: AccessControlService,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
    legacy_token_env_var: str = "PLANS_ADMIN_TOKEN",
) -> tuple[str, str | None]:
    provided_admin_token = resolve_admin_token(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token_query,
    )
    if not provided_admin_token:
        raise HTTPException(status_code=401, detail="Admin token is required.")

    expected_admin_token = os.getenv(legacy_token_env_var, "").strip()
    if expected_admin_token and secrets.compare_digest(provided_admin_token, expected_admin_token):
        return "legacy_token", None

    admin_user = require_admin_user(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token_query,
        access_control_service=access_control_service,
    )
    return "admin_user", admin_user.user_id
