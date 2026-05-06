from __future__ import annotations

from typing import Callable

from app.application.errors import InvalidUserTokenError

PlanTuple = tuple[str, str, int, str, int, int]

DEFAULT_PUBLIC_PLANS: tuple[PlanTuple, ...] = (
    ("essencial", "Essencial", 1, "BRL", 2990, 150),
    ("profissional", "Profissional", 1, "BRL", 3990, 300),
    ("escritorio", "Escritorio", 1, "BRL", 4990, 500),
)

MONTHLY_QUOTA_WINDOW_DAYS = 30
PAID_MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
PAID_MAX_PAGES_PER_FILE = 100


def list_public_plans(
    conn,
    *,
    fetchall: Callable,
    true_value: bool | int,
) -> list[dict[str, str | int]]:
    rows = fetchall(
        conn,
        """
        SELECT
          id,
          code,
          name,
          version,
          currency,
          price_cents,
          billing_period,
          quota_mode,
          quota_limit,
          quota_window_days,
          max_upload_size_bytes,
          max_pages_per_file
        FROM plan_versions
        WHERE is_public = ? AND is_active = ?
        ORDER BY code ASC, version DESC
        """,
        (true_value, true_value),
    )
    items: list[dict[str, str | int]] = []
    for row in rows:
        items.append(
            {
                "id": str(row["id"]),
                "code": str(row["code"]),
                "name": str(row["name"]),
                "version": int(row["version"]),
                "currency": str(row["currency"]),
                "price_cents": int(row["price_cents"]),
                "billing_period": str(row["billing_period"]),
                "quota_mode": str(row["quota_mode"]),
                "quota_limit": int(row["quota_limit"]),
                "quota_window_days": int(row["quota_window_days"]),
                "max_upload_size_bytes": int(row["max_upload_size_bytes"]),
                "max_pages_per_file": int(row["max_pages_per_file"]),
            }
        )
    return items


def activate_user_plan(
    conn,
    *,
    fetchone: Callable,
    execute: Callable,
    true_value: bool | int,
    user_id: str,
    plan_code: str,
    now_iso: str,
    subscription_id: str,
) -> dict[str, str | int]:
    normalized_code = str(plan_code or "").strip().lower()
    if not normalized_code:
        raise ValueError("plan_code is required")
    if fetchone(conn, "SELECT id FROM users WHERE id = ?", (user_id,)) is None:
        raise InvalidUserTokenError

    plan = fetchone(
        conn,
        """
        SELECT
          id,
          code,
          name,
          version,
          quota_mode,
          quota_limit,
          quota_window_days,
          max_upload_size_bytes,
          max_pages_per_file
        FROM plan_versions
        WHERE code = ? AND is_active = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (normalized_code, true_value),
    )
    if plan is None:
        raise ValueError("plan not found")

    execute(
        conn,
        """
        UPDATE user_plan_subscriptions
        SET status = 'ended', ended_at = ?
        WHERE user_id = ? AND status = 'active'
        """,
        (now_iso, user_id),
    )
    execute(
        conn,
        """
        INSERT INTO user_plan_subscriptions (
          id,
          user_id,
          plan_version_id,
          status,
          started_at,
          ended_at
        )
        VALUES (?, ?, ?, 'active', ?, NULL)
        """,
        (subscription_id, user_id, str(plan["id"]), now_iso),
    )
    execute(
        conn,
        """
        INSERT INTO usage (
          identity_type,
          identity_id,
          used_count,
          quota_limit,
          updated_at,
          window_started_at
        )
        VALUES (?, ?, 0, ?, ?, ?)
        ON CONFLICT(identity_type, identity_id)
        DO UPDATE SET
          used_count=excluded.used_count,
          quota_limit=excluded.quota_limit,
          updated_at=excluded.updated_at,
          window_started_at=excluded.window_started_at
        """,
        ("user", user_id, int(plan["quota_limit"]), now_iso, now_iso),
    )
    return {
        "code": str(plan["code"]),
        "name": str(plan["name"]),
        "version": int(plan["version"]),
        "quota_mode": str(plan["quota_mode"]),
        "quota_limit": int(plan["quota_limit"]),
    }


def read_active_user_plan(
    conn,
    *,
    fetchone: Callable,
    user_id: str,
) -> dict[str, str | int] | None:
    row = fetchone(
        conn,
        """
        SELECT
          pv.code,
          pv.name,
          pv.version,
          pv.quota_mode,
          pv.quota_limit,
          pv.quota_window_days,
          pv.max_upload_size_bytes,
          pv.max_pages_per_file
        FROM user_plan_subscriptions ups
        JOIN plan_versions pv ON pv.id = ups.plan_version_id
        WHERE ups.user_id = ? AND ups.status = 'active'
        ORDER BY pv.version DESC
        LIMIT 1
        """,
        (user_id,),
    )
    if row is None:
        return None
    return {
        "code": str(row["code"]),
        "name": str(row["name"]),
        "version": int(row["version"]),
        "quota_mode": str(row["quota_mode"]),
        "quota_limit": int(row["quota_limit"]),
        "quota_window_days": int(row["quota_window_days"]),
        "max_upload_size_bytes": int(row["max_upload_size_bytes"]),
        "max_pages_per_file": int(row["max_pages_per_file"]),
    }


def seed_default_public_plans(
    conn,
    *,
    fetchone: Callable,
    execute: Callable,
    now_iso: str,
    true_value: bool | int,
) -> None:
    for code, name, version, currency, price_cents, quota_limit in DEFAULT_PUBLIC_PLANS:
        existing_version = fetchone(
            conn,
            """
            SELECT id
            FROM plan_versions
            WHERE code = ? AND version = ?
            LIMIT 1
            """,
            (code, version),
        )
        if existing_version is not None:
            continue
        execute(
            conn,
            """
            INSERT INTO plan_versions (
              id,
              code,
              name,
              version,
              currency,
              price_cents,
              billing_period,
              quota_mode,
              quota_limit,
              quota_window_days,
              max_upload_size_bytes,
              max_pages_per_file,
              is_public,
              is_active,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"plan_{code}_v{version}",
                code,
                name,
                version,
                currency,
                price_cents,
                "monthly",
                "pages",
                quota_limit,
                MONTHLY_QUOTA_WINDOW_DAYS,
                PAID_MAX_UPLOAD_SIZE_BYTES,
                PAID_MAX_PAGES_PER_FILE,
                true_value,
                true_value,
                now_iso,
            ),
        )
