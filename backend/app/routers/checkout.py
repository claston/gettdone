import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.application import (
    AccessControlService,
    ContactDeliveryError,
    ContactProviderNotConfiguredError,
    ContactService,
    InvalidUserTokenError,
)
from app.application.checkout_management import (
    CHECKOUT_STATUS_AWAITING_PAYMENT,
    CHECKOUT_STATUS_RELEASED_FOR_USE,
    CHECKOUT_STATUS_REQUESTED,
)
from app.dependencies import get_access_control_service, get_contact_service
from app.schemas import (
    AdminCheckoutIntentHistoryResponse,
    AdminCheckoutIntentItem,
    AdminCheckoutIntentListResponse,
    CheckoutIntentPaymentLinkRequest,
    CheckoutIntentRequest,
    CheckoutIntentResponse,
    CheckoutIntentStatusResponse,
)

router = APIRouter()


def _format_price_brl(price_cents: int) -> str:
    value = int(price_cents)
    reais = value // 100
    cents = value % 100
    return f"R$ {reais},{cents:02d}"


def _next_step_for_status(status: str) -> str:
    if status == CHECKOUT_STATUS_REQUESTED:
        return "SEND_PAYMENT_LINK"
    if status == CHECKOUT_STATUS_AWAITING_PAYMENT:
        return "WAIT_FOR_PAYMENT"
    if status == CHECKOUT_STATUS_RELEASED_FOR_USE:
        return "READY_TO_USE"
    return "REVIEW_ORDER"


def _resolve_admin_token(
    *,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
) -> str:
    if x_admin_token and x_admin_token.strip():
        return x_admin_token.strip()
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    return (admin_token_query or "").strip()


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


def _require_admin_access(
    *,
    access_control_service: AccessControlService,
    x_admin_token: str | None,
    authorization: str | None,
    admin_token_query: str | None,
) -> tuple[str, str | None]:
    provided_admin_token = _resolve_admin_token(
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token_query,
    )
    if not provided_admin_token:
        raise HTTPException(status_code=401, detail="Admin token is required.")
    expected_admin_token = os.getenv("PLANS_ADMIN_TOKEN", "").strip()
    if expected_admin_token and secrets.compare_digest(provided_admin_token, expected_admin_token):
        return "legacy_token", None

    try:
        admin_user = access_control_service.get_user_by_token(user_token=provided_admin_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid admin token.")
    if not access_control_service.is_user_admin(user_id=admin_user.user_id):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return "admin_user", admin_user.user_id


@router.post("/checkout/intents", response_model=CheckoutIntentResponse, status_code=202)
async def create_checkout_intent(
    payload: CheckoutIntentRequest,
    access_control_service: AccessControlService = Depends(get_access_control_service),
    contact_service: ContactService = Depends(get_contact_service),
) -> CheckoutIntentResponse:
    clean_user_token = payload.user_token.strip()
    clean_name = payload.name.strip()
    clean_email = payload.email.strip().lower()
    clean_whatsapp = payload.whatsapp.strip()
    clean_plan_code = payload.plan_code.strip().lower()
    clean_document = (payload.document or "").strip()
    clean_notes = (payload.notes or "").strip()

    if not clean_user_token:
        raise HTTPException(status_code=400, detail="user_token is required.")
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail="Terms must be accepted before checkout.")
    if not clean_name or not clean_email or not clean_whatsapp or not clean_plan_code:
        raise HTTPException(status_code=400, detail="name, email, whatsapp, and plan_code are required.")
    if "@" not in clean_email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    try:
        user = access_control_service.get_user_by_token(user_token=clean_user_token)
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid user token.")

    try:
        intent = access_control_service.create_checkout_intent(
            user_id=user.user_id,
            plan_code=clean_plan_code,
            customer_name=clean_name,
            customer_email=clean_email,
            customer_whatsapp=clean_whatsapp,
            customer_document=clean_document or None,
            customer_notes=clean_notes or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    plan_price = _format_price_brl(int(intent["price_cents"]))
    admin_subject = f"[Checkout] New order {intent['id']} - {intent['plan_name']}"
    admin_text_lines = [
        "A new plan order was received.",
        "",
        f"Order: {intent['id']}",
        f"User ID: {user.user_id}",
        f"Plan: {intent['plan_name']} ({intent['plan_code']})",
        f"Price: {plan_price}/{intent['billing_period']}",
        f"Name: {clean_name}",
        f"Email: {clean_email}",
        f"WhatsApp: {clean_whatsapp}",
        f"Document: {clean_document or '-'}",
        "",
        "Notes:",
        clean_notes or "-",
        "",
        "Next step: send payment link and, after payment, activate the plan.",
    ]
    customer_subject = f"We received your plan order ({intent['plan_name']})"
    customer_text_lines = [
        "We received your plan order at OFX Simples.",
        "",
        f"Order: {intent['id']}",
        f"Plan: {intent['plan_name']}",
        f"Price: {plan_price}/{intent['billing_period']}",
        "",
        "Our team will send your payment link by email.",
        "Plan activation is manual after payment confirmation.",
    ]

    try:
        admin_delivery = await contact_service.send_text_email(
            to_email=contact_service.support_email,
            subject=admin_subject,
            text="\n".join(admin_text_lines),
            reply_to=clean_email,
        )
        customer_delivery = await contact_service.send_text_email(
            to_email=clean_email,
            subject=customer_subject,
            text="\n".join(customer_text_lines),
        )
    except ContactProviderNotConfiguredError:
        raise HTTPException(status_code=503, detail="Checkout contact channel is not configured.")
    except ContactDeliveryError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to deliver checkout emails: {exc}")

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
        admin_delivery_mode=admin_delivery.delivery_mode,
        customer_delivery_mode=customer_delivery.delivery_mode,
        message="Order received. We will send your payment link by email.",
    )


@router.get("/checkout/intents/latest", response_model=CheckoutIntentStatusResponse)
def read_latest_checkout_intent(
    user_token: str = Query(...),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    clean_user_token = user_token.strip()
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


@router.get("/checkout/intents/{intent_id}", response_model=CheckoutIntentStatusResponse)
def read_checkout_intent(
    intent_id: str,
    user_token: str = Query(...),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    clean_intent_id = intent_id.strip()
    clean_user_token = user_token.strip()
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
def set_checkout_intent_payment_link(
    intent_id: str,
    payload: CheckoutIntentPaymentLinkRequest,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None, alias="admin_token"),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    actor_kind, actor_user_id = _require_admin_access(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
    )

    clean_intent_id = intent_id.strip()
    clean_payment_link = payload.payment_link.strip()
    if not clean_intent_id or not clean_payment_link:
        raise HTTPException(status_code=400, detail="intent_id and payment_link are required.")

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
    return _build_checkout_intent_status_response(intent)


@router.get("/admin/checkout/intents", response_model=AdminCheckoutIntentListResponse)
def list_checkout_intents_for_admin(
    status: str = Query(default="open"),
    query: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None, alias="admin_token"),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminCheckoutIntentListResponse:
    _require_admin_access(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
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
    return AdminCheckoutIntentListResponse(
        items=[_build_admin_checkout_intent_item(item) for item in intents],
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
    admin_token: str | None = Query(default=None, alias="admin_token"),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> AdminCheckoutIntentHistoryResponse:
    _require_admin_access(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
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


@router.post("/admin/checkout/intents/{intent_id}/release", response_model=CheckoutIntentStatusResponse)
def release_checkout_intent_for_use(
    intent_id: str,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    admin_token: str | None = Query(default=None, alias="admin_token"),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> CheckoutIntentStatusResponse:
    actor_kind, actor_user_id = _require_admin_access(
        access_control_service=access_control_service,
        x_admin_token=x_admin_token,
        authorization=authorization,
        admin_token_query=admin_token,
    )
    clean_intent_id = intent_id.strip()
    if not clean_intent_id:
        raise HTTPException(status_code=400, detail="intent_id is required.")

    intent = access_control_service.read_checkout_intent_by_id(intent_id=clean_intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Checkout intent not found.")
    user_id = str(intent["user_id"] or "").strip()
    resolved_from_email = False
    if not user_id:
        customer_email = str(intent.get("customer_email") or "").strip().lower()
        if not customer_email:
            raise HTTPException(status_code=400, detail="Cannot release checkout intent without user reference.")
        try:
            user = access_control_service.get_user_by_email(customer_email)
            user_id = user.user_id
            resolved_from_email = True
        except InvalidUserTokenError:
            raise HTTPException(status_code=404, detail="User not found for checkout customer email.")

    try:
        access_control_service.activate_user_plan(
            user_id=user_id,
            plan_code=str(intent["plan_code"]),
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
        if resolved_from_email:
            access_control_service.mark_checkout_intent_released_by_id(
                intent_id=clean_intent_id,
                actor_kind=actor_kind,
                actor_user_id=actor_user_id,
            )
    except InvalidUserTokenError:
        raise HTTPException(status_code=404, detail="User not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    updated = access_control_service.read_checkout_intent_by_id(intent_id=clean_intent_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Checkout intent not found.")
    return _build_checkout_intent_status_response(updated)
