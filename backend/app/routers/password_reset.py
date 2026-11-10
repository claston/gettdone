import logging
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.application import AccessControlService, ContactDeliveryError, ContactProviderNotConfiguredError, ContactService
from app.application.access_control.access_control_password_reset import InvalidPasswordResetTokenError
from app.dependencies import get_access_control_service, get_contact_service
from app.routers.access_control_common import clear_session_cookies
from app.security_baseline import read_bool_env


def require_password_reset_enabled():
    if not read_bool_env("AUTH_PASSWORD_RESET_ENABLED", default=True):
        raise HTTPException(status_code=503, detail="Recuperação de senha temporariamente indisponível. Tente novamente mais tarde.")


router = APIRouter(dependencies=[Depends(require_password_reset_enabled)])
logger = logging.getLogger(__name__)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)


def reset_frontend_url() -> str:
    value = os.getenv("AUTH_PASSWORD_RESET_FRONTEND_URL", "").strip() or "https://www.ofxsimples.com.br/redefinir-senha.html"
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.fragment
            or parsed.username or parsed.password
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})):
        raise RuntimeError("AUTH_PASSWORD_RESET_FRONTEND_URL must be an HTTPS URL (HTTP allowed only on localhost)")
    return value


@router.post("/auth/forgot-password", status_code=202)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    service: AccessControlService = Depends(get_access_control_service),
    mail: ContactService = Depends(get_contact_service),
):
    # Resolve configuration before account lookup to keep errors independent of account existence.
    frontend_url = reset_frontend_url()
    issued = service.password_reset.issue(email=payload.email)
    if issued:
        # Respond before contacting the provider so its latency does not reveal account existence.
        background_tasks.add_task(deliver_reset_email, service, mail, issued, frontend_url)
    return JSONResponse(status_code=202, headers={"Cache-Control": "no-store"}, content={
        "message": "Se houver uma conta com senha para este e-mail, você receberá um link para redefini-la."
    })


async def deliver_reset_email(service, mail, issued, frontend_url):
    try:
        await mail.send_text_email(
            to_email=issued.email,
            subject="Redefina sua senha no OFX Simples",
            text=("Olá!\n\nRecebemos uma solicitação para redefinir sua senha.\n"
                  f"Redefina sua senha: {frontend_url}#token={issued.token}\n\n"
                  "Este link é válido por 30 minutos e pode ser usado uma única vez.\n"
                  "Se você não fez esta solicitação, ignore esta mensagem. Sua senha permanece a mesma."),
        )
    except (ContactDeliveryError, ContactProviderNotConfiguredError):
        service.password_reset.mark_delivery(token_id=issued.token_id, sent=False)
        logger.warning("password_reset_delivery_failed token_id=%s", issued.token_id)
    else:
        service.password_reset.mark_delivery(token_id=issued.token_id, sent=True)
        logger.info("password_reset_delivery_succeeded token_id=%s", issued.token_id)


@router.post("/auth/reset-password")
def reset_password(payload: ResetPasswordRequest, service: AccessControlService = Depends(get_access_control_service)):
    try:
        service.password_reset.confirm(token=payload.token, password=payload.password)
    except InvalidPasswordResetTokenError:
        raise HTTPException(status_code=400, detail="Link inválido ou expirado. Solicite uma nova recuperação de senha.") from None
    response = JSONResponse(headers={"Cache-Control": "no-store"}, content={
        "message": "Senha redefinida com sucesso. Entre com sua nova senha."
    })
    clear_session_cookies(response)
    return response
