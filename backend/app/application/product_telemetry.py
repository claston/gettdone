from __future__ import annotations

from uuid import uuid4

from app.application.conversion.identity import IdentityContext

CLIENT_PRODUCT_EVENT_TYPES = frozenset({"plans_view", "plan_cta_click", "checkout_view"})
PRODUCT_EVENT_TYPES = CLIENT_PRODUCT_EVENT_TYPES | {"download"}
DOWNLOAD_FORMATS = frozenset({"ofx", "xlsx", "csv"})


def record_product_event(
    service,
    *,
    identity: IdentityContext,
    event_type: str,
    page_path: str | None = None,
    plan_code: str | None = None,
    processing_id: str | None = None,
    download_format: str | None = None,
) -> str:
    normalized_event_type = str(event_type or "").strip().lower()
    if normalized_event_type not in PRODUCT_EVENT_TYPES:
        raise ValueError("Unsupported product event type")

    identity_type = "registered" if identity.identity_type == "user" else "anonymous"
    normalized_format = str(download_format or "").strip().lower() or None
    if normalized_event_type == "download" and normalized_format not in DOWNLOAD_FORMATS:
        raise ValueError("Unsupported download format")
    if normalized_event_type != "download":
        normalized_format = None

    event_id = f"pe_{uuid4().hex}"
    with service._lock:
        with service._connect() as conn:
            service._execute(
                conn,
                """
                INSERT INTO product_events (
                    id, event_type, identity_type, identity_id, created_at,
                    page_path, plan_code, processing_id, download_format
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    normalized_event_type,
                    identity_type,
                    identity.identity_id,
                    service.now_provider().isoformat(),
                    _clean_optional(page_path, 200),
                    _clean_optional(plan_code, 80),
                    _clean_optional(processing_id, 120),
                    normalized_format,
                ),
            )
            conn.commit()
    return event_id


def _clean_optional(value: str | None, max_length: int) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:max_length] or None
