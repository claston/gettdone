import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.application import (
    AccessControlService,
    ContactDeliveryError,
    ContactProviderNotConfiguredError,
    ContactService,
    InvalidUserTokenError,
)
from app.application.checkout_management import (
    CHECKOUT_STATUS_AWAITING_PAYMENT,
    CHECKOUT_STATUS_PENDING_LEGACY,
    CHECKOUT_STATUS_RELEASED_FOR_USE,
    CHECKOUT_STATUS_REQUESTED,
)
from app.application.purchase_email_service import deliver_purchase_notification, render_purchase_email, site_url
from app.application.purchase_flow import PurchaseConflict, PurchaseFlow
from app.dependencies import get_access_control_service, get_contact_service
from app.routers.access_control_common import (
    SESSION_ACCESS_COOKIE_NAME,
    require_admin_actor,
    resolve_user_token_with_session,
)
from app.routers.pro_security import ProSessionRateLimiter
from app.schemas import (
    AdminCheckoutIntentHistoryResponse,
    AdminCheckoutIntentItem,
    AdminCheckoutIntentListResponse,
    CheckoutIntentPaymentLinkRequest,
    CheckoutIntentRequest,
    CheckoutIntentResponse,
    CheckoutIntentStatusResponse,
    CheckoutOpenIntentsResponse,
)
from app.security_baseline import read_bool_env

router = APIRouter()


def _format_price_brl(price_cents: int) -> str:
    value = int(price_cents)
    reais = value // 100
    cents = value % 100
    return f"R$ {reais},{cents:02d}"


def _is_safe_payment_link(value: str) -> bool:
    if not value or len(value) > 2048 or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(hostname)
        and not parsed.username
        and not parsed.password
    )


def _next_step_for_status(status: str) -> str:
    if status in (CHECKOUT_STATUS_REQUESTED, CHECKOUT_STATUS_PENDING_LEGACY):
        return "SEND_PAYMENT_LINK"
    if status == CHECKOUT_STATUS_AWAITING_PAYMENT:
        return "WAIT_FOR_PAYMENT"
    if status == CHECKOUT_STATUS_RELEASED_FOR_USE:
        return "READY_TO_USE"
    return "REVIEW_ORDER"


def _build_checkout_intent_status_response(intent: dict[str, str | int | None]) -> CheckoutIntentStatusResponse:
    status = str(intent["status"])
    return CheckoutIntentStatusResponse(
        intent_id=str(intent["id"]),
        status=status,
        created_at=str(intent["created_at"]),
        updated_at=str(intent["updated_at"]),
        plan_code=str(intent["plan_code"]),
        plan_name=str(intent["plan_name"]),
        price_cents=int(intent["price_cents"]),
        currency=str(intent["currency"]),
        billing_period=str(intent["billing_period"]),
        payment_link=(str(intent["payment_link"]) if intent.get("payment_link") else None),
        payment_link_sent_at=(str(intent["payment_link_sent_at"]) if intent.get("payment_link_sent_at") else None),
        released_at=(str(intent["released_at"]) if intent.get("released_at") else None),
        next_step=_next_step_for_status(status),
    )


def _build_admin_checkout_intent_item(intent: dict[str, str | int | None]) -> AdminCheckoutIntentItem:
    status = str(intent["status"])
    return AdminCheckoutIntentItem(
        intent_id=str(intent["id"]),
        status=status,
        next_step=_next_step_for_status(status),
        created_at=str(intent["created_at"]),
        updated_at=str(intent["updated_at"]),
        user_id=str(intent["user_id"] or ""),
        plan_code=str(intent["plan_code"]),
        plan_name=str(intent["plan_name"]),
        price_cents=int(intent["price_cents"]),
        currency=str(intent["currency"]),
        billing_period=str(intent["billing_period"]),
        customer_name=str(intent["customer_name"]),
        customer_email=str(intent["customer_email"]),
        customer_whatsapp=str(intent["customer_whatsapp"]),
        customer_document=(str(intent["customer_document"]) if intent.get("customer_document") else None),
        customer_notes=(str(intent["customer_notes"]) if intent.get("customer_notes") else None),
        payment_link=(str(intent["payment_link"]) if intent.get("payment_link") else None),
        payment_link_sent_at=(str(intent["payment_link_sent_at"]) if intent.get("payment_link_sent_at") else None),
        released_at=(str(intent["released_at"]) if intent.get("released_at") else None),
    )

@router.post("/checkout/intents", response_model=CheckoutIntentResponse, status_code=202)
async def create_checkout_intent(
    payload: CheckoutIntentRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> CheckoutIntentResponse:
    clean_user_token = resolve_user_token_with_session(
        access_control_service=access_control_service,
        authorization=authorization,
        explicit_user_token=payload.user_token,
        access_cookie_token=access_cookie_token,
    )
    clean_name = payload.name.strip()
    clean_email = payload.email.strip().lower()
    clean_whatsapp = payload.whatsapp.strip()
    clean_plan_code = payload.plan_code.strip().lower()
    clean_document = (payload.document or "").strip()
    clean_notes = (payload.notes or "").strip()

    if not clean_user_token and not read_bool_env("CHECKOUT_GUEST_ENABLED", default=True):
        raise HTTPException(status_code=503, detail="Entre na sua conta para enviar o pedido.")
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail="Terms must be accepted before checkout.")
    if not clean_name or not clean_email or not clean_whatsapp or not clean_plan_code:
        raise HTTPException(status_code=400, detail="name, email, whatsapp, and plan_code are required.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    if not clean_user_token:
        component = access_control_service.checkout
        if not hasattr(component, "guest_rate_limiter"):
            component.guest_rate_limiter = ProSessionRateLimiter(limit=10, window_seconds=3600)
        try:
            component.guest_rate_limiter.enforce(request.client.host if request.client else "unknown")
        except HTTPException as exc:
            raise HTTPException(status_code=429, detail="Muitos pedidos enviados. Aguarde e tente novamente.",
                                headers=exc.headers) from None

    user = None
    if clean_user_token:
        try:
            user = access_control_service.get_user_by_token(user_token=clean_user_token)
        except InvalidUserTokenError:
            raise HTTPException(status_code=401, detail="Invalid user token.") from None
        if user.email.strip().lower() != clean_email:
            raise HTTPException(status_code=400, detail="Use o e-mail da sua conta para solicitar este plano.")

    try:
        intent = access_control_service.create_checkout_intent(
            user_id=user.user_id if user else None,
            plan_code=clean_plan_code,
            customer_name=clean_name,
            customer_email=clean_email,
            customer_whatsapp=clean_whatsapp,
            customer_document=clean_document or None,
            customer_notes=clean_notes or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    admin_delivery = await deliver_purchase_notification(
        access_control_service, contact_service, str(intent["id"]), "admin_receipt")
    customer_delivery = await deliver_purchase_notification(
        access_control_service, contact_service, str(intent["id"]), "customer_receipt")

    status = str(intent["status"])
    return CheckoutIntentResponse(
        intent_id=str(intent["id"]),
        status=status,
        created_at=str(intent["created_at"]),
        updated_at=str(intent["updated_at"]),
        plan_code=str(intent["plan_code"]),
        plan_name=str(intent["plan_name"]),
        price_cents=int(intent["price_cents"]),
        currency=str(intent["currency"]),
        billing_period=str(intent["billing_period"]),
        payment_link=(str(intent["payment_link"]) if intent.get("payment_link") else None),
        payment_link_sent_at=(str(intent["payment_link_sent_at"]) if intent.get("payment_link_sent_at") else None),
        released_at=(str(intent["released_at"]) if intent.get("released_at") else None),
        next_step=_next_step_for_status(status),
        admin_delivery_mode=admin_delivery,
        customer_delivery_mode=customer_delivery,
        message="Pedido recebido. Enviaremos o link de pagamento por e-mail.",
    )


@router.get("/checkout/intents/latest", response_model=CheckoutIntentStatusResponse)
def read_latest_checkout_intent(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    clean_user_token = resolve_user_token_with_session(
        access_control_service=access_control_service,
        authorization=authorization,
        explicit_user_token=user_token,
        access_cookie_token=access_cookie_token,
    )
    if not clean_user_token:
        raise HTTPException(status_code=400, detail="user_token is required.")

    try:
        user = access_control_service.get_user_by_token(user_token=clean_user_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    intent = access_control_service.read_latest_checkout_intent_for_user(
        user_id=user.user_id,
        customer_email=user.email,
    )
    if intent is None:
        raise HTTPException(status_code=404, detail="No checkout order found for this user.")
    return _build_checkout_intent_status_response(intent)


@router.get("/checkout/intents/open", response_model=CheckoutOpenIntentsResponse)
def read_open_checkout_intents(
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutOpenIntentsResponse:
    token = resolve_user_token_with_session(
        access_control_service=access_control_service, authorization=authorization,
        explicit_user_token=user_token, access_cookie_token=access_cookie_token,
    )
    if not token:
        raise HTTPException(status_code=400, detail="user_token is required.")
    try:
        user = access_control_service.get_user_by_token(user_token=token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")
    items = access_control_service.list_open_checkout_intents_for_user(user_id=user.user_id, customer_email=user.email)
    return CheckoutOpenIntentsResponse(items=[_build_checkout_intent_status_response(item) for item in items])


@router.get("/checkout/intents/{intent_id}", response_model=CheckoutIntentStatusResponse)
def read_checkout_intent(
    intent_id: str,
    authorization: str | None = Header(default=None),
    user_token: str | None = Query(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    clean_intent_id = intent_id.strip()
    clean_user_token = resolve_user_token_with_session(
        access_control_service=access_control_service,
        authorization=authorization,
        explicit_user_token=user_token,
        access_cookie_token=access_cookie_token,
    )
    if not clean_intent_id or not clean_user_token:
        raise HTTPException(status_code=400, detail="intent_id and user_token are required.")

    try:
        user = access_control_service.get_user_by_token(user_token=clean_user_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    intent = access_control_service.read_checkout_intent_for_user(
        intent_id=clean_intent_id,
        user_id=user.user_id,
        customer_email=user.email,
    )
    if intent is None:
        raise HTTPException(status_code=404, detail="Checkout intent not found.")
    return _build_checkout_intent_status_response(intent)


@router.post("/admin/checkout/intents/{intent_id}/payment-link", response_model=CheckoutIntentStatusResponse)
async def set_checkout_intent_payment_link(
    intent_id: str,
    payload: CheckoutIntentPaymentLinkRequest,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> CheckoutIntentStatusResponse:
    actor_kind, actor_user_id = require_admin_actor(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )

    clean_intent_id = intent_id.strip()
    clean_payment_link = payload.payment_link.strip()
    if not clean_intent_id or not clean_payment_link:
        raise HTTPException(status_code=400, detail="intent_id and payment_link are required.")
    if not _is_safe_payment_link(clean_payment_link):
        raise HTTPException(status_code=400, detail="payment_link must be a valid HTTP or HTTPS URL.")

    intent = access_control_service.read_checkout_intent_by_id(intent_id=clean_intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="checkout intent not found")
    if str(intent["status"]) == CHECKOUT_STATUS_RELEASED_FOR_USE:
        raise HTTPException(status_code=400, detail="checkout intent is already released")

    flow = PurchaseFlow(access_control_service)
    notification = flow.claim_notification(clean_intent_id, "payment", retry=True)
    if notification is None:
        raise HTTPException(status_code=409, detail="O envio deste pedido já está em andamento.")
    token_id = None
    try:
        token_id, token = flow.issue_token(clean_intent_id, "payment")
        message = render_purchase_email("payment", intent, payment_url=clean_payment_link,
                                        payment_page_url=site_url() + "/pagamento.html#token=" + token)
        delivery = await contact_service.send_text_email(
            to_email=str(intent["customer_email"]),
            **message,
            reply_to=contact_service.support_email,
            idempotency_key=f"{notification['id']}/{notification['attempts']}",
        )
    except (ContactProviderNotConfiguredError, ValueError):
        flow.finish_notification(notification, sent=False, token_id=token_id)
        raise HTTPException(status_code=503, detail="O envio de e-mails não está configurado. O link não foi enviado.") from None
    except ContactDeliveryError:
        flow.finish_notification(notification, sent=False, token_id=token_id)
        raise HTTPException(status_code=502, detail="Não foi possível enviar o link por e-mail. Tente novamente.") from None
    if delivery.delivery_mode == "dry_run":
        flow.finish_notification(notification, sent=False, token_id=token_id)
        raise HTTPException(status_code=503, detail="O envio de e-mails está em modo de teste. O link não foi enviado.")

    # Persist the sent timestamp and audit event only after provider acceptance.
    try:
        intent = access_control_service.mark_checkout_intent_awaiting_payment(
            intent_id=clean_intent_id,
            payment_link=clean_payment_link,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    flow.finish_notification(notification, sent=True, provider_message_id=delivery.provider_message_id)
    return _build_checkout_intent_status_response(intent)


@router.get("/admin/checkout/intents", response_model=AdminCheckoutIntentListResponse)
def list_checkout_intents_for_admin(
    status: str = Query(default="open"),
    query: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminCheckoutIntentListResponse:
    require_admin_actor(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )

    normalized_status = status.strip().lower()
    statuses: tuple[str, ...] | None
    if normalized_status in {"open", "pending"}:
        statuses = (CHECKOUT_STATUS_REQUESTED, CHECKOUT_STATUS_AWAITING_PAYMENT)
    elif normalized_status in {"requested", "request"}:
        statuses = (CHECKOUT_STATUS_REQUESTED,)
    elif normalized_status in {"awaiting_payment", "awaiting"}:
        statuses = (CHECKOUT_STATUS_AWAITING_PAYMENT,)
    elif normalized_status in {"released", "released_for_use"}:
        statuses = (CHECKOUT_STATUS_RELEASED_FOR_USE,)
    elif normalized_status in {"all", "*"}:
        statuses = None
    else:
        raise HTTPException(status_code=400, detail="Unsupported status filter.")

    intents, total = access_control_service.list_checkout_intents_for_admin(
        statuses=statuses,
        query=query,
        limit=limit,
        offset=offset,
    )
    notifications = PurchaseFlow(access_control_service).notification_states([item["id"] for item in intents])
    return AdminCheckoutIntentListResponse(
        items=[_admin_item_with_notifications(item, notifications) for item in intents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/admin/checkout/intents/{intent_id}/history", response_model=AdminCheckoutIntentHistoryResponse)
def list_checkout_intent_history_for_admin(
    intent_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminCheckoutIntentHistoryResponse:
    require_admin_actor(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        access_cookie_token=access_cookie_token,
    )
    clean_intent_id = intent_id.strip()
    if not clean_intent_id:
        raise HTTPException(status_code=400, detail="intent_id is required.")
    intent = access_control_service.read_checkout_intent_by_id(intent_id=clean_intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Checkout intent not found.")
    events = access_control_service.list_checkout_intent_events_for_admin(intent_id=clean_intent_id, limit=limit)
    return AdminCheckoutIntentHistoryResponse(
        intent_id=clean_intent_id,
        items=[
            {
                "event_id": str(item["id"]),
                "intent_id": str(item["intent_id"]),
                "event_type": str(item["event_type"]),
                "event_message": str(item["event_message"] or ""),
                "actor_kind": str(item["actor_kind"]),
                "actor_user_id": (str(item["actor_user_id"]) if item.get("actor_user_id") else None),
                "payload_json": (str(item["payload_json"]) if item.get("payload_json") else None),
                "created_at": str(item["created_at"]),
            }
            for item in events
        ],
    )


class ReleasePurchaseRequest(BaseModel):
    create_account: bool = False


@router.get("/admin/checkout/intents/{intent_id}/release-preview")
def preview_purchase_release(
    intent_id: str,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    service: AccessControlService = Depends(get_access_control_service),
):
    require_admin_actor(access_control_service=service, x_admin_token=x_admin_token,
                        authorization=authorization, access_cookie_token=access_cookie_token)
    intent = service.read_checkout_intent_by_id(intent_id=intent_id)
    if not intent:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    with service._lock, service._connect() as conn:
        user = service._fetchone(conn, "SELECT id, is_active, initial_access_pending FROM users WHERE lower(email) = ?",
                                (intent["customer_email"].strip().lower(),))
    return JSONResponse(headers={"Cache-Control": "no-store"}, content={
        "customer_email": intent["customer_email"], "account_exists": user is not None,
        "initial_access_pending": bool(user and user["initial_access_pending"]),
        "account_suspended": bool(user and not user["is_active"] and not user["initial_access_pending"]),
        "account_mismatch": bool(intent["user_id"] and (not user or user["id"] != intent["user_id"])),
    })


def _notification_response(service, intent_id):
    intent = service.read_checkout_intent_by_id(intent_id=intent_id)
    result = _build_checkout_intent_status_response(intent)
    notification = PurchaseFlow(service).notification(intent_id)
    result.notification_status = notification["status"] if notification else None
    return result


@router.post("/admin/checkout/intents/{intent_id}/release", response_model=CheckoutIntentStatusResponse)
async def release_checkout_intent_for_use(
    intent_id: str,
    payload: ReleasePurchaseRequest | None = None,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> CheckoutIntentStatusResponse:
    actor_kind, actor_user_id = require_admin_actor(
        access_control_service=access_control_service, x_admin_token=x_admin_token,
        authorization=authorization, access_cookie_token=access_cookie_token)
    if access_control_service.read_checkout_intent_by_id(intent_id=intent_id) is None:
        raise HTTPException(status_code=404, detail="Checkout intent not found.")
    try:
        PurchaseFlow(access_control_service).release(intent_id,
            create_account=bool(payload and payload.create_account
                                and read_bool_env("CHECKOUT_ADMIN_PROVISIONING_ENABLED", default=True)),
            actor_kind=actor_kind, actor_user_id=actor_user_id)
    except PurchaseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    # Retrying the release never resets quota or resends a previously attempted email.
    await deliver_purchase_notification(access_control_service, contact_service, intent_id, "release")
    return _notification_response(access_control_service, intent_id)


@router.post("/admin/checkout/intents/{intent_id}/resend-notification", response_model=CheckoutIntentStatusResponse)
async def resend_purchase_notification(
    intent_id: str,
    kind: str = Query(default="release", pattern="^(release|admin_receipt|customer_receipt)$"),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
):
    actor_kind, actor_user_id = require_admin_actor(
        access_control_service=access_control_service, x_admin_token=x_admin_token,
        authorization=authorization, access_cookie_token=access_cookie_token)
    intent = access_control_service.read_checkout_intent_by_id(intent_id=intent_id)
    if not intent:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    if kind == "release" and intent["status"] != "RELEASED_FOR_USE":
        raise HTTPException(status_code=400, detail="Libere o plano antes de reenviar o acesso.")
    flow = PurchaseFlow(access_control_service)
    with access_control_service._lock, access_control_service._connect() as conn:
        flow.event(conn, intent_id, "NOTIFICATION_RETRY_REQUESTED", actor_kind, actor_user_id, {"kind": kind})
        conn.commit()
    await deliver_purchase_notification(access_control_service, contact_service, intent_id, kind, retry=True)
    return _notification_response(access_control_service, intent_id)


class PurchaseTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class InitialAccessRequest(PurchaseTokenRequest):
    password: str = Field(min_length=8, max_length=256)
    accepted_terms: bool = False


@router.post("/auth/initial-access")
def confirm_initial_access(payload: InitialAccessRequest,
                           service: AccessControlService = Depends(get_access_control_service)):
    try:
        PurchaseFlow(service).confirm(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return JSONResponse(headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                        content={"message": "Senha definida. Entre com seu e-mail e a nova senha."})


@router.post("/checkout/payment-page")
def read_payment_page(payload: PurchaseTokenRequest,
                      service: AccessControlService = Depends(get_access_control_service)):
    try:
        data = PurchaseFlow(service).payment_page(payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return JSONResponse(headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}, content=data)


def _admin_item_with_notifications(intent, notifications):
    item = _build_admin_checkout_intent_item(intent)
    item.notification_status = notifications.get((intent["id"], "release"))
    statuses = [notifications.get((intent["id"], kind), "pending") for kind in ("admin_receipt", "customer_receipt")]
    item.receipt_status = ("failed" if "failed" in statuses else
                           "sent" if statuses and all(value == "sent" for value in statuses) else "pending")
    return item
