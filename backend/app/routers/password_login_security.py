from fastapi import HTTPException

from app.application.login_rate_limit import LoginRateLimitedError, enforce_password_login_budget


def enforce_login_limit(service, request, email):
    try:
        enforce_password_login_budget(service, email=email, ip_address=request.client.host if request.client else None)
    except LoginRateLimitedError as exc:
        raise HTTPException(429, "Muitas tentativas de login. Aguarde e tente novamente.",
                            headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"}) from None
