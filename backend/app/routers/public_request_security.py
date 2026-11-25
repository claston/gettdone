from fastapi import HTTPException

from app.application.errors import InvalidUserTokenError
from app.application.public_request_budget import PublicBudgetExceeded, enforce_public_budget
from app.routers.access_control_common import resolve_user_token_with_session


def enforce_upload_budget(service, request, *, authorization, user_token, access_cookie_token):
    try:
        token = resolve_user_token_with_session(
            access_control_service=service, authorization=authorization,
            explicit_user_token=user_token, access_cookie_token=access_cookie_token,
        )
        if token:
            service.get_user_by_token(token)
            return
    except InvalidUserTokenError:
        # Invalid credentials must not exempt a request from the public budget.
        # The normal conversion authentication path still rejects the token.
        pass
    enforce_public_request(service, request, kind="conversion")


def enforce_public_request(service, request, *, kind, identity=None, units=1, operation_id=None):
    if identity is not None and identity.identity_type != "anonymous":
        return
    try:
        enforce_public_budget(
            service, kind=kind, ip_address=request.client.host if request.client else None,
            units=units, operation_id=operation_id,
        )
    except PublicBudgetExceeded as exc:
        message = (
            "O limite de processamento gratuito desta rede foi atingido. Entre na sua conta ou aguarde a renovação do limite."
            if kind == "conversion" and exc.scope == "network"
            else "Há muitas solicitações no momento. Aguarde e tente novamente."
        )
        raise HTTPException(
            429, detail={"code": "public_request_limit", "message": message},
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
