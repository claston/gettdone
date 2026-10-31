import logging
import os
from html import escape
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app.application.access_control import AccessControlService
from app.application.contact_service import ContactService
from app.application.errors import ContactDeliveryError, ContactProviderNotConfiguredError
from app.application.marketing_email_service import (
    TEST_RECIPIENT,
    TEST_TEMPLATE_ID,
    InvalidMarketingUnsubscribeTokenError,
    MarketingConfigurationError,
    MarketingEmailService,
    MarketingRecipientUnavailableError,
)
from app.dependencies import get_access_control_service, get_contact_service
from app.routers.access_control_common import SESSION_ACCESS_COOKIE_NAME, require_admin_user

router = APIRouter()
logger = logging.getLogger(__name__)


class MarketingTestEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class MarketingTestEmailResponse(BaseModel):
    recipient: str
    template_id: str
    delivery_mode: str
    provider_message_id: str | None


@router.post("/admin/marketing/test-email", response_model=MarketingTestEmailResponse)
async def send_marketing_test_email(
    payload: MarketingTestEmailRequest,
    response: Response,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control: AccessControlService = Depends(get_access_control_service),
    mailer: ContactService = Depends(get_contact_service),
) -> MarketingTestEmailResponse:
    admin = require_admin_user(
        x_admin_token=x_admin_token, authorization=authorization,
        access_cookie_token=access_cookie_token, access_control_service=access_control,
    )
    try:
        delivery = await MarketingEmailService(access_control).send_test(
            mailer=mailer,
            public_base_url=os.getenv("MARKETING_PUBLIC_BASE_URL", "https://www.ofxsimples.com.br").strip(),
            request_id=str(payload.request_id),
        )
    except MarketingRecipientUnavailableError:
        raise HTTPException(409, "O usuário a@a.com.br precisa existir, estar ativo, ter e-mail verificado e consentimento de marketing.")
    except (ContactProviderNotConfiguredError, MarketingConfigurationError):
        raise HTTPException(503, "O envio de marketing não está configurado. Verifique o Resend e a URL pública de descadastro.")
    except ContactDeliveryError:
        logger.warning("marketing_test_delivery_failed admin_id=%s template_id=%s", admin.user_id, TEST_TEMPLATE_ID)
        raise HTTPException(502, "Não foi possível confirmar o envio pelo Resend. Tente novamente para verificar o mesmo disparo.")
    response.headers["Cache-Control"] = "no-store"
    logger.info("marketing_test_delivery_finished admin_id=%s template_id=%s mode=%s provider_message_id=%s",
                admin.user_id, TEST_TEMPLATE_ID, delivery.delivery_mode, delivery.provider_message_id)
    return MarketingTestEmailResponse(
        recipient=TEST_RECIPIENT, template_id=TEST_TEMPLATE_ID,
        delivery_mode=delivery.delivery_mode, provider_message_id=delivery.provider_message_id,
    )


def _unsubscribe_page(*, token: str | None = None, status_code: int = 200, message: str = "") -> HTMLResponse:
    content = f"<p>{escape(message)}</p>" if message else (
        '<p>Confirme para deixar de receber conteúdos, novidades e ofertas do OFX Simples.</p>'
        f'<form method="post" action="?token={escape(token or "", quote=True)}">'
        '<button type="submit">Não quero receber mais e-mails</button></form>'
    )
    return HTMLResponse(
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><title>Preferências de e-mail | OFX Simples</title>'
        '<link rel="stylesheet" href="/admin.css">'
        '</head><body><main class="admin-page"><section class="card"><h1>Preferências de e-mail</h1>'
        + content + '</section></main></body></html>',
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"},
    )


@router.get("/marketing/unsubscribe", response_class=HTMLResponse)
def confirm_marketing_unsubscribe(
    token: str = Query(min_length=1, max_length=256),
    access_control: AccessControlService = Depends(get_access_control_service),
) -> HTMLResponse:
    try:
        MarketingEmailService(access_control).validate_unsubscribe_token(token)
    except InvalidMarketingUnsubscribeTokenError:
        return _unsubscribe_page(status_code=400, message="Link de cancelamento inválido. Use o link do e-mail mais recente.")
    # GET never changes consent: email security scanners may open links automatically.
    return _unsubscribe_page(token=token)


@router.post("/marketing/unsubscribe", response_class=HTMLResponse)
def unsubscribe_marketing(
    token: str = Query(min_length=1, max_length=256),
    access_control: AccessControlService = Depends(get_access_control_service),
) -> HTMLResponse:
    try:
        MarketingEmailService(access_control).unsubscribe(token)
    except InvalidMarketingUnsubscribeTokenError:
        return _unsubscribe_page(status_code=400, message="Link de cancelamento inválido. Use o link do e-mail mais recente.")
    return _unsubscribe_page(
        message="Recebimento de e-mails de marketing cancelado. As mensagens necessárias à sua conta continuam disponíveis.",
    )
