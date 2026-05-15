from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.application.checkout_management import (
    create_checkout_intent as create_checkout_intent_query,
)
from app.application.checkout_management import (
    list_checkout_intent_events_for_admin as list_checkout_intent_events_for_admin_query,
)
from app.application.checkout_management import (
    list_checkout_intents_for_admin as list_checkout_intents_for_admin_query,
)
from app.application.checkout_management import (
    mark_checkout_intent_awaiting_payment as mark_checkout_intent_awaiting_payment_query,
)
from app.application.checkout_management import (
    mark_checkout_intent_released_by_id as mark_checkout_intent_released_by_id_query,
)
from app.application.checkout_management import (
    mark_latest_checkout_intent_released_for_user_plan as mark_latest_checkout_intent_released_for_user_plan_query,
)
from app.application.checkout_management import (
    read_checkout_intent_by_id as read_checkout_intent_by_id_query,
)
from app.application.checkout_management import (
    read_checkout_intent_for_user as read_checkout_intent_for_user_query,
)
from app.application.checkout_management import (
    read_latest_checkout_intent_for_user as read_latest_checkout_intent_for_user_query,
)
from app.application.conversion_history import (
    list_user_conversions as list_user_conversions_query,
)
from app.application.conversion_history import (
    record_user_conversion as record_user_conversion_query,
)
from app.application.plan_management import (
    activate_user_plan as activate_user_plan_query,
)
from app.application.plan_management import (
    list_public_plans as list_public_plans_query,
)

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class AccessControlCheckoutComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def record_user_conversion(
        self,
        *,
        user_id: str,
        processing_id: str,
        filename: str,
        model: str,
        conversion_type: str,
        status: str,
        transactions_count: int | None,
        pages_count: int | None = None,
        created_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        with self._service._lock:
            with self._service._connect() as conn:
                record_user_conversion_query(
                    conn,
                    execute=self._service._execute,
                    now_iso=self._service.now_provider().isoformat(),
                    user_id=user_id,
                    processing_id=processing_id,
                    filename=filename,
                    model=model,
                    conversion_type=conversion_type,
                    status=status,
                    transactions_count=transactions_count,
                    pages_count=pages_count,
                    created_at=created_at,
                    expires_at=expires_at,
                )
                conn.commit()

    def list_user_conversions(self, *, user_id: str, limit: int = 20) -> list[dict[str, str | int]]:
        with self._service._lock:
            with self._service._connect() as conn:
                return list_user_conversions_query(
                    conn,
                    fetchall=self._service._fetchall,
                    now_provider=self._service.now_provider,
                    user_id=user_id,
                    limit=limit,
                )

    def list_public_plans(self) -> list[dict[str, str | int]]:
        with self._service._lock:
            with self._service._connect() as conn:
                return list_public_plans_query(
                    conn,
                    fetchall=self._service._fetchall,
                    true_value=self._service._true_value(),
                )

    def activate_user_plan(
        self,
        *,
        user_id: str,
        plan_code: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int]:
        now_iso = self._service.now_provider().isoformat()

        with self._service._lock:
            with self._service._connect() as conn:
                activated = activate_user_plan_query(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    true_value=self._service._true_value(),
                    user_id=user_id,
                    plan_code=plan_code,
                    now_iso=now_iso,
                    subscription_id=f"sub_{uuid4().hex[:16]}",
                )
                released_intent_id = mark_latest_checkout_intent_released_for_user_plan_query(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    now_iso=now_iso,
                    user_id=user_id,
                    plan_code=plan_code,
                )
                if released_intent_id:
                    self._service._append_checkout_intent_event_with_conn(
                        conn,
                        intent_id=released_intent_id,
                        event_type="PLAN_RELEASED",
                        event_message="Plan released for user.",
                        actor_kind=actor_kind,
                        actor_user_id=actor_user_id,
                        payload={
                            "user_id": user_id,
                            "plan_code": plan_code,
                        },
                        created_at=now_iso,
                    )
                conn.commit()
                self._service._invalidate_active_plan_cache(user_id)
        return activated

    def create_checkout_intent(
        self,
        *,
        user_id: str,
        plan_code: str,
        customer_name: str,
        customer_email: str,
        customer_whatsapp: str,
        customer_document: str | None = None,
        customer_notes: str | None = None,
    ) -> dict[str, str | int]:
        now_iso = self._service.now_provider().isoformat()
        intent_id = f"chk_{uuid4().hex[:16]}"
        with self._service._lock:
            with self._service._connect() as conn:
                intent = create_checkout_intent_query(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    true_value=self._service._true_value(),
                    now_iso=now_iso,
                    intent_id=intent_id,
                    user_id=user_id,
                    plan_code=plan_code,
                    customer_name=customer_name,
                    customer_email=customer_email,
                    customer_whatsapp=customer_whatsapp,
                    customer_document=customer_document,
                    customer_notes=customer_notes,
                )
                self._service._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="ORDER_REQUESTED",
                    event_message="Checkout order requested.",
                    actor_kind="customer",
                    actor_user_id=user_id,
                    payload={
                        "plan_code": str(intent["plan_code"]),
                        "customer_email": customer_email,
                    },
                    created_at=now_iso,
                )
                conn.commit()
        return intent

    def read_checkout_intent_for_user(
        self,
        *,
        intent_id: str,
        user_id: str,
        customer_email: str | None = None,
    ) -> dict[str, str | int | None] | None:
        with self._service._lock:
            with self._service._connect() as conn:
                return read_checkout_intent_for_user_query(
                    conn,
                    fetchone=self._service._fetchone,
                    intent_id=intent_id,
                    user_id=user_id,
                    customer_email=customer_email,
                )

    def read_latest_checkout_intent_for_user(
        self,
        *,
        user_id: str,
        customer_email: str | None = None,
    ) -> dict[str, str | int | None] | None:
        with self._service._lock:
            with self._service._connect() as conn:
                return read_latest_checkout_intent_for_user_query(
                    conn,
                    fetchone=self._service._fetchone,
                    user_id=user_id,
                    customer_email=customer_email,
                )

    def mark_checkout_intent_awaiting_payment(
        self,
        *,
        intent_id: str,
        payment_link: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int | None]:
        now_iso = self._service.now_provider().isoformat()
        with self._service._lock:
            with self._service._connect() as conn:
                intent = mark_checkout_intent_awaiting_payment_query(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    now_iso=now_iso,
                    intent_id=intent_id,
                    payment_link=payment_link,
                )
                self._service._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="PAYMENT_LINK_SENT",
                    event_message="Payment link sent to customer.",
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    payload={
                        "payment_link": payment_link,
                    },
                    created_at=now_iso,
                )
                conn.commit()
                return intent

    def read_checkout_intent_by_id(self, *, intent_id: str) -> dict[str, str | int | None] | None:
        with self._service._lock:
            with self._service._connect() as conn:
                return read_checkout_intent_by_id_query(
                    conn,
                    fetchone=self._service._fetchone,
                    intent_id=intent_id,
                )

    def mark_checkout_intent_released_by_id(
        self,
        *,
        intent_id: str,
        actor_kind: str = "system",
        actor_user_id: str | None = None,
    ) -> dict[str, str | int | None]:
        now_iso = self._service.now_provider().isoformat()
        with self._service._lock:
            with self._service._connect() as conn:
                intent = mark_checkout_intent_released_by_id_query(
                    conn,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    now_iso=now_iso,
                    intent_id=intent_id,
                )
                self._service._append_checkout_intent_event_with_conn(
                    conn,
                    intent_id=str(intent["id"]),
                    event_type="PLAN_RELEASED",
                    event_message="Plan released for user.",
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    payload={
                        "plan_code": str(intent["plan_code"]),
                    },
                    created_at=now_iso,
                )
                conn.commit()
                return intent

    def list_checkout_intents_for_admin(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, str | int | None]], int]:
        with self._service._lock:
            with self._service._connect() as conn:
                return list_checkout_intents_for_admin_query(
                    conn,
                    fetchall=self._service._fetchall,
                    fetchone=self._service._fetchone,
                    statuses=statuses,
                    query=query,
                    limit=limit,
                    offset=offset,
                )

    def list_checkout_intent_events_for_admin(
        self,
        *,
        intent_id: str,
        limit: int = 100,
    ) -> list[dict[str, str | None]]:
        with self._service._lock:
            with self._service._connect() as conn:
                return list_checkout_intent_events_for_admin_query(
                    conn,
                    fetchall=self._service._fetchall,
                    intent_id=intent_id,
                    limit=limit,
                )
