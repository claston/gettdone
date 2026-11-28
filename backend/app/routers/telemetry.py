from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.application import AccessControlService, InvalidUserTokenError
from app.application.product_telemetry import CLIENT_PRODUCT_EVENT_TYPES, WORKSPACE_EVENT_TYPES
from app.dependencies import get_access_control_service
from app.posthog_analytics import CLIENT_EVENTS, record_event
from app.routers.auth_session import (
    ANONYMOUS_IDENTITY_COOKIE_NAME,
    SESSION_ACCESS_COOKIE_NAME,
    resolve_anonymous_fingerprint_with_cookie,
    resolve_user_token_with_session,
)
from app.routers.pro_security import PRO_CLIENT_HEADER_NAME, require_pro_browser_request
from app.schemas import ProductEventRequest

router = APIRouter()


class AnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str = Field(max_length=40)
    page_path: str = Field(default="/other", max_length=200)


@router.post("/telemetry/analytics", status_code=status.HTTP_202_ACCEPTED)
def record_analytics_event(payload: AnalyticsEventRequest) -> Response:
    if payload.event_type not in CLIENT_EVENTS:
        raise HTTPException(status_code=400, detail="Evento de uso inválido.")
    record_event(payload.event_type, page_path=payload.page_path)
    return Response(status_code=202, headers={"Cache-Control": "no-store"})


@router.post("/telemetry/events", status_code=status.HTTP_202_ACCEPTED)
def record_client_product_event(
    payload: ProductEventRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    access_cookie_token: str | None = Cookie(default=None, alias=SESSION_ACCESS_COOKIE_NAME),
    anonymous_cookie_token: str | None = Cookie(default=None, alias=ANONYMOUS_IDENTITY_COOKIE_NAME),
    access_control_service: AccessControlService = Depends(get_access_control_service),
) -> Response:
    event_type = payload.event_type.strip().lower()
    if event_type not in CLIENT_PRODUCT_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported product event type.")
    workspace_event = event_type in WORKSPACE_EVENT_TYPES
    if workspace_event:
        require_pro_browser_request(request, request.headers.get(PRO_CLIENT_HEADER_NAME))
    try:
        user_token = resolve_user_token_with_session(
            access_control_service=access_control_service,
            authorization=authorization,
            explicit_user_token=None,
            access_cookie_token=access_cookie_token,
        )
        if (workspace_event or event_type == "client_area_view") and not user_token:
            raise HTTPException(status_code=401, detail="A registered product identity is required.")
        anonymous_fingerprint = ""
        if not user_token:
            anonymous_fingerprint = resolve_anonymous_fingerprint_with_cookie(
                access_control_service=access_control_service,
                anonymous_cookie_token=anonymous_cookie_token,
                legacy_fingerprint=None,
            )
        if not user_token and not anonymous_fingerprint:
            raise HTTPException(status_code=401, detail="A product identity is required.")
        identity = access_control_service.resolve_identity(
            anonymous_fingerprint=anonymous_fingerprint or None,
            user_token=user_token or None,
        )
    except InvalidUserTokenError:
        raise HTTPException(status_code=401, detail="Invalid product identity.") from None

    access_control_service.record_product_event(
        identity=identity,
        event_type=event_type,
        page_path="/review" if workspace_event else payload.page_path,
        plan_code=None if workspace_event else payload.plan_code,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED, headers={"Cache-Control": "no-store"})
