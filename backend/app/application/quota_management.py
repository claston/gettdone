from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.application.errors import QuotaExceededError


class IdentityLike(Protocol):
    identity_type: str
    identity_id: str
    quota_limit: int
    quota_window_days: int


@dataclass(frozen=True)
class UsageSnapshot:
    used_count: int
    window_started_at: datetime


def require_quota_available(
    *,
    used_count: int,
    quota_limit: int,
    required_units: int = 1,
) -> None:
    units = max(1, int(required_units))
    if (int(used_count) + units) > int(quota_limit):
        raise QuotaExceededError


def compute_remaining_quota(*, used_count: int, quota_limit: int) -> int:
    return max(int(quota_limit) - int(used_count), 0)


def compute_quota_reset_at(*, window_started_at: datetime, quota_window_days: int) -> str:
    reset_at = window_started_at + timedelta(days=max(1, int(quota_window_days)))
    return reset_at.isoformat()


def read_usage_snapshot(
    conn,
    *,
    identity: IdentityLike,
    now_provider,
    fetchone,
    execute,
    parse_usage_datetime,
    is_quota_window_expired,
) -> UsageSnapshot:
    now = now_provider()
    row = fetchone(
        conn,
        """
        SELECT used_count, quota_limit, updated_at, window_started_at
        FROM usage
        WHERE identity_type = ? AND identity_id = ?
        """,
        (identity.identity_type, identity.identity_id),
    )
    if row is None:
        started_at = now.isoformat()
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
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identity.identity_type,
                identity.identity_id,
                0,
                identity.quota_limit,
                started_at,
                started_at,
            ),
        )
        return UsageSnapshot(used_count=0, window_started_at=now)

    used_count = int(row["used_count"] or 0)
    window_started_at = parse_usage_datetime(
        str(row["window_started_at"] or row["updated_at"] or now.isoformat()),
        fallback=now,
    )

    if is_quota_window_expired(window_started_at, now, quota_window_days=identity.quota_window_days):
        window_started_at = now
        used_count = 0
        started_at = window_started_at.isoformat()
        execute(
            conn,
            """
            UPDATE usage
            SET used_count = ?, quota_limit = ?, updated_at = ?, window_started_at = ?
            WHERE identity_type = ? AND identity_id = ?
            """,
            (
                used_count,
                identity.quota_limit,
                started_at,
                started_at,
                identity.identity_type,
                identity.identity_id,
            ),
        )

    return UsageSnapshot(used_count=used_count, window_started_at=window_started_at)


def persist_consumed_usage(
    conn,
    *,
    identity: IdentityLike,
    snapshot: UsageSnapshot,
    consumed_units: int,
    now_provider,
    execute,
) -> UsageSnapshot:
    units = max(1, int(consumed_units))
    require_quota_available(
        used_count=snapshot.used_count,
        quota_limit=identity.quota_limit,
        required_units=units,
    )
    next_used_count = int(snapshot.used_count) + units
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
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(identity_type, identity_id)
        DO UPDATE SET
          used_count=excluded.used_count,
          quota_limit=excluded.quota_limit,
          updated_at=excluded.updated_at,
          window_started_at=excluded.window_started_at
        """,
        (
            identity.identity_type,
            identity.identity_id,
            next_used_count,
            identity.quota_limit,
            now_provider().isoformat(),
            snapshot.window_started_at.isoformat(),
        ),
    )
    return UsageSnapshot(
        used_count=next_used_count,
        window_started_at=snapshot.window_started_at,
    )
