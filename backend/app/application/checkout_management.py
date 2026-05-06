from __future__ import annotations

CHECKOUT_STATUS_REQUESTED = "REQUESTED"
CHECKOUT_STATUS_AWAITING_PAYMENT = "AWAITING_PAYMENT"
CHECKOUT_STATUS_RELEASED_FOR_USE = "RELEASED_FOR_USE"
CHECKOUT_STATUS_PENDING_LEGACY = "pending"

OPEN_CHECKOUT_STATUSES: tuple[str, ...] = (
    CHECKOUT_STATUS_REQUESTED,
    CHECKOUT_STATUS_AWAITING_PAYMENT,
    CHECKOUT_STATUS_PENDING_LEGACY,
)


def create_checkout_intent(
    conn,
    *,
    fetchone,
    execute,
    true_value: bool | int,
    now_iso: str,
    intent_id: str,
    user_id: str,
    plan_code: str,
    customer_name: str,
    customer_email: str,
    customer_whatsapp: str,
    customer_document: str | None = None,
    customer_notes: str | None = None,
) -> dict[str, str | int | None]:
    normalized_code = str(plan_code or "").strip().lower()
    normalized_user_id = str(user_id or "").strip()
    clean_name = str(customer_name or "").strip()
    clean_email = str(customer_email or "").strip().lower()
    clean_whatsapp = str(customer_whatsapp or "").strip()
    clean_document = str(customer_document or "").strip()
    clean_notes = str(customer_notes or "").strip()

    if not normalized_user_id:
        raise ValueError("user_id is required")
    if not normalized_code:
        raise ValueError("plan_code is required")
    if not clean_name or not clean_email or not clean_whatsapp:
        raise ValueError("name, email, and whatsapp are required")

    active_plan = fetchone(
        conn,
        """
        SELECT pv.code
        FROM user_plan_subscriptions ups
        JOIN plan_versions pv ON pv.id = ups.plan_version_id
        WHERE ups.user_id = ? AND ups.status = 'active'
        ORDER BY pv.version DESC
        LIMIT 1
        """,
        (normalized_user_id,),
    )
    if active_plan is not None and str(active_plan["code"]).strip().lower() == normalized_code:
        raise ValueError("You already have this plan active.")

    open_intent = fetchone(
        conn,
        """
        SELECT id
        FROM checkout_intents
        WHERE user_id = ? AND plan_code = ? AND status IN (?, ?, ?)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            normalized_user_id,
            normalized_code,
            CHECKOUT_STATUS_REQUESTED,
            CHECKOUT_STATUS_AWAITING_PAYMENT,
            CHECKOUT_STATUS_PENDING_LEGACY,
        ),
    )
    if open_intent is not None:
        raise ValueError("You already have an open order for this plan.")

    plan = fetchone(
        conn,
        """
        SELECT code, name, price_cents, currency, billing_period
        FROM plan_versions
        WHERE code = ? AND is_active = ? AND is_public = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (normalized_code, true_value, true_value),
    )
    if plan is None:
        raise ValueError("plan not found")

    execute(
        conn,
        """
        INSERT INTO checkout_intents (
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          customer_name,
          customer_email,
          customer_whatsapp,
          customer_document,
          customer_notes,
          payment_link,
          payment_link_sent_at,
          released_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            now_iso,
            now_iso,
            CHECKOUT_STATUS_REQUESTED,
            normalized_user_id,
            str(plan["code"]),
            str(plan["name"]),
            int(plan["price_cents"]),
            str(plan["currency"]),
            str(plan["billing_period"]),
            clean_name,
            clean_email,
            clean_whatsapp,
            clean_document or None,
            clean_notes or None,
            None,
            None,
            None,
        ),
    )

    return {
        "id": intent_id,
        "status": CHECKOUT_STATUS_REQUESTED,
        "created_at": now_iso,
        "updated_at": now_iso,
        "plan_code": str(plan["code"]),
        "plan_name": str(plan["name"]),
        "price_cents": int(plan["price_cents"]),
        "currency": str(plan["currency"]),
        "billing_period": str(plan["billing_period"]),
        "payment_link": None,
        "payment_link_sent_at": None,
        "released_at": None,
    }


def read_checkout_intent_for_user(
    conn,
    *,
    fetchone,
    intent_id: str,
    user_id: str,
    customer_email: str | None = None,
) -> dict[str, str | int | None] | None:
    normalized_intent_id = str(intent_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_customer_email = str(customer_email or "").strip().lower()
    if not normalized_intent_id or not normalized_user_id:
        return None
    row = fetchone(
        conn,
        """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          customer_name,
          customer_email,
          customer_whatsapp,
          customer_document,
          customer_notes,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
        WHERE
          id = ?
          AND (
            user_id = ?
            OR ((user_id IS NULL OR user_id = '') AND lower(customer_email) = ?)
          )
        LIMIT 1
        """,
        (normalized_intent_id, normalized_user_id, normalized_customer_email),
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
        "user_id": str(row["user_id"]),
        "plan_code": str(row["plan_code"]),
        "plan_name": str(row["plan_name"]),
        "price_cents": int(row["price_cents"]),
        "currency": str(row["currency"]),
        "billing_period": str(row["billing_period"]),
        "customer_name": str(row["customer_name"]),
        "customer_email": str(row["customer_email"]),
        "customer_whatsapp": str(row["customer_whatsapp"]),
        "customer_document": str(row["customer_document"] or "") or None,
        "customer_notes": str(row["customer_notes"] or "") or None,
        "payment_link": str(row["payment_link"] or "") or None,
        "payment_link_sent_at": str(row["payment_link_sent_at"] or "") or None,
        "released_at": str(row["released_at"] or "") or None,
    }


def read_latest_checkout_intent_for_user(
    conn,
    *,
    fetchone,
    user_id: str,
    customer_email: str | None = None,
) -> dict[str, str | int | None] | None:
    normalized_user_id = str(user_id or "").strip()
    normalized_customer_email = str(customer_email or "").strip().lower()
    if not normalized_user_id:
        return None
    row = fetchone(
        conn,
        """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          customer_name,
          customer_email,
          customer_whatsapp,
          customer_document,
          customer_notes,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
        WHERE
          user_id = ?
          OR ((user_id IS NULL OR user_id = '') AND lower(customer_email) = ?)
        ORDER BY
          CASE
            WHEN status = ? AND payment_link IS NOT NULL AND payment_link <> '' THEN 0
            WHEN status = ? THEN 1
            WHEN status = ? THEN 2
            WHEN status = ? THEN 3
            ELSE 4
          END,
          created_at DESC
        LIMIT 1
        """,
        (
            normalized_user_id,
            normalized_customer_email,
            CHECKOUT_STATUS_AWAITING_PAYMENT,
            CHECKOUT_STATUS_AWAITING_PAYMENT,
            CHECKOUT_STATUS_REQUESTED,
            CHECKOUT_STATUS_RELEASED_FOR_USE,
        ),
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
        "user_id": str(row["user_id"]),
        "plan_code": str(row["plan_code"]),
        "plan_name": str(row["plan_name"]),
        "price_cents": int(row["price_cents"]),
        "currency": str(row["currency"]),
        "billing_period": str(row["billing_period"]),
        "customer_name": str(row["customer_name"]),
        "customer_email": str(row["customer_email"]),
        "customer_whatsapp": str(row["customer_whatsapp"]),
        "customer_document": str(row["customer_document"] or "") or None,
        "customer_notes": str(row["customer_notes"] or "") or None,
        "payment_link": str(row["payment_link"] or "") or None,
        "payment_link_sent_at": str(row["payment_link_sent_at"] or "") or None,
        "released_at": str(row["released_at"] or "") or None,
    }


def mark_checkout_intent_awaiting_payment(
    conn,
    *,
    fetchone,
    execute,
    now_iso: str,
    intent_id: str,
    payment_link: str,
) -> dict[str, str | int | None]:
    normalized_intent_id = str(intent_id or "").strip()
    clean_payment_link = str(payment_link or "").strip()
    if not normalized_intent_id:
        raise ValueError("intent_id is required")
    if not clean_payment_link:
        raise ValueError("payment_link is required")

    existing = fetchone(
        conn,
        """
        SELECT id, status
        FROM checkout_intents
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_intent_id,),
    )
    if existing is None:
        raise ValueError("checkout intent not found")
    current_status = str(existing["status"])
    if current_status == CHECKOUT_STATUS_RELEASED_FOR_USE:
        raise ValueError("checkout intent is already released")

    execute(
        conn,
        """
        UPDATE checkout_intents
        SET
          status = ?,
          payment_link = ?,
          payment_link_sent_at = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            CHECKOUT_STATUS_AWAITING_PAYMENT,
            clean_payment_link,
            now_iso,
            now_iso,
            normalized_intent_id,
        ),
    )

    row = fetchone(
        conn,
        """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_intent_id,),
    )
    assert row is not None
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
        "user_id": str(row["user_id"]),
        "plan_code": str(row["plan_code"]),
        "plan_name": str(row["plan_name"]),
        "price_cents": int(row["price_cents"]),
        "currency": str(row["currency"]),
        "billing_period": str(row["billing_period"]),
        "payment_link": str(row["payment_link"] or "") or None,
        "payment_link_sent_at": str(row["payment_link_sent_at"] or "") or None,
        "released_at": str(row["released_at"] or "") or None,
    }


def mark_latest_checkout_intent_released_for_user_plan(
    conn,
    *,
    fetchone,
    execute,
    now_iso: str,
    user_id: str,
    plan_code: str,
) -> str | None:
    normalized_user_id = str(user_id or "").strip()
    normalized_plan_code = str(plan_code or "").strip().lower()
    if not normalized_user_id or not normalized_plan_code:
        return None

    row = fetchone(
        conn,
        """
        SELECT id
        FROM checkout_intents
        WHERE
          user_id = ?
          AND plan_code = ?
          AND status IN (?, ?, ?)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            normalized_user_id,
            normalized_plan_code,
            CHECKOUT_STATUS_REQUESTED,
            CHECKOUT_STATUS_AWAITING_PAYMENT,
            CHECKOUT_STATUS_PENDING_LEGACY,
        ),
    )
    if row is None:
        return None

    intent_id = str(row["id"])
    execute(
        conn,
        """
        UPDATE checkout_intents
        SET
          status = ?,
          released_at = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            CHECKOUT_STATUS_RELEASED_FOR_USE,
            now_iso,
            now_iso,
            intent_id,
        ),
    )
    return intent_id


def mark_checkout_intent_released_by_id(
    conn,
    *,
    fetchone,
    execute,
    now_iso: str,
    intent_id: str,
) -> dict[str, str | int | None]:
    normalized_intent_id = str(intent_id or "").strip()
    if not normalized_intent_id:
        raise ValueError("intent_id is required")
    row = fetchone(
        conn,
        """
        SELECT id
        FROM checkout_intents
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_intent_id,),
    )
    if row is None:
        raise ValueError("checkout intent not found")
    execute(
        conn,
        """
        UPDATE checkout_intents
        SET
          status = ?,
          released_at = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            CHECKOUT_STATUS_RELEASED_FOR_USE,
            now_iso,
            now_iso,
            normalized_intent_id,
        ),
    )
    updated = fetchone(
        conn,
        """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_intent_id,),
    )
    assert updated is not None
    return {
        "id": str(updated["id"]),
        "created_at": str(updated["created_at"]),
        "updated_at": str(updated["updated_at"]),
        "status": str(updated["status"]),
        "user_id": str(updated["user_id"] or ""),
        "plan_code": str(updated["plan_code"]),
        "plan_name": str(updated["plan_name"]),
        "price_cents": int(updated["price_cents"]),
        "currency": str(updated["currency"]),
        "billing_period": str(updated["billing_period"]),
        "payment_link": str(updated["payment_link"] or "") or None,
        "payment_link_sent_at": str(updated["payment_link_sent_at"] or "") or None,
        "released_at": str(updated["released_at"] or "") or None,
    }


def read_checkout_intent_by_id(
    conn,
    *,
    fetchone,
    intent_id: str,
) -> dict[str, str | int | None] | None:
    normalized_intent_id = str(intent_id or "").strip()
    if not normalized_intent_id:
        return None
    row = fetchone(
        conn,
        """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          customer_name,
          customer_email,
          customer_whatsapp,
          customer_document,
          customer_notes,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_intent_id,),
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
        "user_id": str(row["user_id"] or ""),
        "plan_code": str(row["plan_code"]),
        "plan_name": str(row["plan_name"]),
        "price_cents": int(row["price_cents"]),
        "currency": str(row["currency"]),
        "billing_period": str(row["billing_period"]),
        "customer_name": str(row["customer_name"]),
        "customer_email": str(row["customer_email"]),
        "customer_whatsapp": str(row["customer_whatsapp"]),
        "customer_document": str(row["customer_document"] or "") or None,
        "customer_notes": str(row["customer_notes"] or "") or None,
        "payment_link": str(row["payment_link"] or "") or None,
        "payment_link_sent_at": str(row["payment_link_sent_at"] or "") or None,
        "released_at": str(row["released_at"] or "") or None,
    }


def list_checkout_intents_for_admin(
    conn,
    *,
    fetchall,
    fetchone,
    statuses: tuple[str, ...] | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, str | int | None]], int]:
    normalized_limit = max(1, min(int(limit), 200))
    normalized_offset = max(0, int(offset))
    base_query = """
        SELECT
          id,
          created_at,
          updated_at,
          status,
          user_id,
          plan_code,
          plan_name,
          price_cents,
          currency,
          billing_period,
          customer_name,
          customer_email,
          customer_whatsapp,
          customer_document,
          customer_notes,
          payment_link,
          payment_link_sent_at,
          released_at
        FROM checkout_intents
    """
    where_clauses: list[str] = []
    params: list[str | int] = []
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        where_clauses.append(f"status IN ({placeholders})")
        params.extend([str(status).strip() for status in statuses])
    normalized_query = str(query or "").strip().lower()
    if normalized_query:
        where_clauses.append(
            "(lower(id) LIKE ? OR lower(customer_name) LIKE ? OR lower(customer_email) LIKE ? OR lower(plan_name) LIKE ?)"
        )
        like_value = f"%{normalized_query}%"
        params.extend([like_value, like_value, like_value, like_value])
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    count_query = f"SELECT COUNT(1) AS total FROM ({base_query}) filtered"
    total_row = fetchone(conn, count_query, tuple(params))
    total = int(total_row["total"]) if total_row is not None else 0

    base_query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.append(normalized_limit)
    params.append(normalized_offset)

    rows = fetchall(conn, base_query, tuple(params))
    items: list[dict[str, str | int | None]] = []
    for row in rows:
        items.append(
            {
                "id": str(row["id"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "status": str(row["status"]),
                "user_id": str(row["user_id"] or ""),
                "plan_code": str(row["plan_code"]),
                "plan_name": str(row["plan_name"]),
                "price_cents": int(row["price_cents"]),
                "currency": str(row["currency"]),
                "billing_period": str(row["billing_period"]),
                "customer_name": str(row["customer_name"]),
                "customer_email": str(row["customer_email"]),
                "customer_whatsapp": str(row["customer_whatsapp"]),
                "customer_document": str(row["customer_document"] or "") or None,
                "customer_notes": str(row["customer_notes"] or "") or None,
                "payment_link": str(row["payment_link"] or "") or None,
                "payment_link_sent_at": str(row["payment_link_sent_at"] or "") or None,
                "released_at": str(row["released_at"] or "") or None,
            }
        )
    return items, total


def insert_checkout_intent_event(
    conn,
    *,
    execute,
    event_id: str,
    intent_id: str,
    event_type: str,
    event_message: str,
    actor_kind: str,
    actor_user_id: str | None,
    payload_json: str | None,
    created_at: str,
) -> None:
    execute(
        conn,
        """
        INSERT INTO checkout_intent_events (
          id,
          intent_id,
          event_type,
          event_message,
          actor_kind,
          actor_user_id,
          payload_json,
          created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            intent_id,
            event_type,
            event_message,
            actor_kind,
            actor_user_id,
            payload_json,
            created_at,
        ),
    )


def list_checkout_intent_events_for_admin(
    conn,
    *,
    fetchall,
    intent_id: str,
    limit: int = 100,
) -> list[dict[str, str | None]]:
    normalized_intent_id = str(intent_id or "").strip()
    normalized_limit = max(1, min(int(limit), 500))
    if not normalized_intent_id:
        return []
    rows = fetchall(
        conn,
        """
        SELECT
          id,
          intent_id,
          event_type,
          event_message,
          actor_kind,
          actor_user_id,
          payload_json,
          created_at
        FROM checkout_intent_events
        WHERE intent_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (normalized_intent_id, normalized_limit),
    )
    return [
        {
            "id": str(row["id"]),
            "intent_id": str(row["intent_id"]),
            "event_type": str(row["event_type"]),
            "event_message": str(row["event_message"] or ""),
            "actor_kind": str(row["actor_kind"]),
            "actor_user_id": str(row["actor_user_id"] or "") or None,
            "payload_json": str(row["payload_json"] or "") or None,
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]
