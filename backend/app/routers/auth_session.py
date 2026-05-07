import os

from app.application import AccessControlService, InvalidSessionTokenError, InvalidUserTokenError

SESSION_ACCESS_COOKIE_NAME = os.getenv("SESSION_ACCESS_COOKIE_NAME", "__Host-ofx_at").strip() or "__Host-ofx_at"


def resolve_user_token_with_session(
    *,
    access_control_service: AccessControlService,
    authorization: str | None,
    explicit_user_token: str | None,
    access_cookie_token: str | None,
) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer

    explicit = (explicit_user_token or "").strip()
    if explicit:
        return explicit

    cookie_token = (access_cookie_token or "").strip()
    if not cookie_token:
        return ""
    try:
        user = access_control_service.get_user_by_session_access_token(cookie_token)
        return user.token
    except InvalidSessionTokenError:
        raise InvalidUserTokenError from None
