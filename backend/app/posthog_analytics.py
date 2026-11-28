"""Explicit, consent-gated analytics. Never pass document payloads to this module."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import BoundedSemaphore
from urllib.parse import unquote
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)
PROJECT_TOKEN = "phc_wacv5NWEwf9HtzfB5325HYF9NsuxpkmnbdBkCYHYk52x"
HOST = "https://us.i.posthog.com"
_context: ContextVar[dict | None] = ContextVar("posthog_context", default=None)
_send_slots = BoundedSemaphore(8)
CLIENT_EVENTS = {"page_view", "upload_started", "preview_viewed", "quota_notice_viewed"}
SERVER_EVENTS = {
    "plans_view", "plan_cta_click", "checkout_view", "client_area_view", "download",
    "workspace_opened", "conversion_selected", "suspicious_filter_used", "transaction_edited",
    "conversion_reviewed", "batch_export_requested", "course_cta_view", "course_cta_click",
    "course_page_view", "course_outbound_click",
}
PUBLIC_PAGES = {
    "/", "/index.html", "/planos.html", "/checkout.html", "/client-area.html", "/login.html",
    "/signup.html", "/ofx-convert.html", "/convert.html", "/conciliador.html", "/ofx-landing.html",
    "/converter-pdf-para-ofx.html", "/converter-pdf-para-excel.html", "/curso-notas-explicativas.html",
    "/review", "/conversions", "/documents", "/financial", "/settings", "/login",
    "/escritorio", "/works", "/dashboard", "/reconciliation", "/clients", "/periods", "/account",
}


def safe_page(value: str) -> str:
    path = str(value).split("?", 1)[0].split("#", 1)[0]
    if path in PUBLIC_PAGES:
        return path
    for prefix in ("/conversions/", "/documents/", "/blog/", "/review/", "/client-review/", "/works/", "/financial/"):
        if path.startswith(prefix):
            return prefix + ":id"
    return "/other"


def enabled() -> bool:
    default = "true" if os.getenv("APP_ENV", "").strip().lower() in {"production", "prod"} else "false"
    return os.getenv("POSTHOG_ENABLED", default).strip().lower() == "true"


def record_event(event: str, *, user_id: str | None = None, **properties) -> None:
    context = _context.get()
    if context is None or event not in CLIENT_EVENTS | SERVER_EVENTS or len(context["events"]) >= 16:
        return
    if user_id and not context.get("identified") and len(context["events"]) > 14:
        return
    safe = {"environment": os.getenv("APP_ENV", "development"), "$geoip_disable": True,
            "$process_person_profile": False, "surface": context["surface"]}
    if properties.get("page_path"):
        safe["page_path"] = safe_page(properties["page_path"])
    if properties.get("download_format") in {"csv", "xlsx", "ofx"}:
        safe["download_format"] = properties["download_format"]
    # Only configured product categories, never arbitrary form fields.
    if properties.get("plan_code") in {"free", "gratuito", "essencial", "profissional", "escritorio", "pro", "escritorio_documentos"}:
        safe["plan_code"] = properties["plan_code"]
    distinct_id = context["anonymous_id"]
    if user_id:
        distinct_id = "user_" + hashlib.sha256(user_id.encode()).hexdigest()
        if not context.get("identified"):
            context["events"].append(_event("$identify", distinct_id, {
                "$anon_distinct_id": context["anonymous_id"], "$geoip_disable": True,
            }))
            context["identified"] = True
        safe["$process_person_profile"] = True
    context["events"].append(_event(event, distinct_id, safe))


def _event(name: str, distinct_id: str, properties: dict) -> dict:
    return {"event": name, "distinct_id": distinct_id, "properties": properties,
            "timestamp": datetime.now(timezone.utc).isoformat(), "uuid": str(uuid4())}


async def send_batch(events: list[dict]) -> None:
    # Workers import record_event without the API's HTTP dependencies installed.
    import httpx

    if not _send_slots.acquire(blocking=False):
        logger.warning("posthog_capacity_drop")
        return
    try:
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:
            response = await client.post(HOST + "/batch/", json={
                "api_key": os.getenv("POSTHOG_PROJECT_TOKEN", PROJECT_TOKEN), "batch": events,
            })
            response.raise_for_status()
    finally:
        _send_slots.release()


class AnalyticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in {"/auth/session/logout", "/auth/session/logout-all"}:
            original_send = send

            async def clear_identity(message):
                if message["type"] == "http.response.start" and message["status"] < 400:
                    message.setdefault("headers", []).append((
                        b"set-cookie", b"ofx_analytics_id=; Path=/; Max-Age=0; SameSite=Lax",
                    ))
                await original_send(message)

            send = clear_identity
        if scope["type"] != "http" or not enabled():
            return await self.app(scope, receive, send)
        from starlette.requests import Request

        request = Request(scope)
        try:
            prefs = json.loads(unquote(request.cookies.get("ofx_privacy_preferences_v1", "")[:2048]))
            allowed = isinstance(prefs, dict) and prefs.get("analytics") is True
            anonymous_id = str(UUID(request.cookies.get("ofx_analytics_id", "")))
        except (ValueError, TypeError, AttributeError):
            allowed, anonymous_id = False, ""
        if not allowed:
            return await self.app(scope, receive, send)
        context = {"anonymous_id": anonymous_id, "events": [],
                   "surface": "pro" if request.headers.get("x-ofx-pro-client") == "web" else "site"}
        token = _context.set(context)
        try:
            # Sending happens after the response body, including streamed reports.
            await self.app(scope, receive, send)
            if context["events"]:
                try:
                    await send_batch(context["events"])
                except Exception:
                    # No request URLs, cookies, document data or exception payloads in logs.
                    logger.warning("posthog_delivery_failed")
        finally:
            _context.reset(token)
