from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.application.black_friday import monthly_window
from app.application.errors import FileTooLargeError
from app.application.quota_management import (
    compute_quota_reset_at,
    compute_remaining_quota,
    persist_consumed_usage,
    read_usage_snapshot,
    require_quota_available,
)

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService, IdentityContext


class AccessControlQuotaComponent:
    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int) -> None:
        if len(raw_bytes) > max(1, int(max_upload_size_bytes)):
            raise FileTooLargeError

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None:
        usage = self._service._read_usage(identity)
        require_quota_available(
            used_count=int(usage["used_count"]),
            quota_limit=identity.quota_limit,
            required_units=required_units,
        )

    def consume_quota(
        self,
        identity: IdentityContext,
        *,
        consumed_units: int = 1,
        idempotency_key: str | None = None,
    ) -> int:
        normalized_key = (idempotency_key or "").strip()
        if len(normalized_key) > 200:
            raise ValueError("Quota consumption idempotency key is too long.")
        with self._service._lock:
            with self._service._connect() as conn:
                if self._service._use_postgres:
                    self._ensure_and_lock_postgres_usage(conn, identity=identity)
                if normalized_key and not self._reserve_consumption(
                    conn,
                    identity=identity,
                    idempotency_key=normalized_key,
                    consumed_units=consumed_units,
                ):
                    usage = read_usage_snapshot(
                        conn,
                        identity=identity,
                        now_provider=self._service.now_provider,
                        fetchone=self._service._fetchone,
                        execute=self._service._execute,
                        parse_usage_datetime=self._service._parse_usage_datetime,
                        is_quota_window_expired=self._service._is_quota_window_expired,
                    )
                    conn.commit()
                    return compute_remaining_quota(
                        used_count=usage.used_count,
                        quota_limit=identity.quota_limit,
                    )
                snapshot = read_usage_snapshot(
                    conn,
                    identity=identity,
                    now_provider=self._service.now_provider,
                    fetchone=self._service._fetchone,
                    execute=self._service._execute,
                    parse_usage_datetime=self._service._parse_usage_datetime,
                    is_quota_window_expired=self._service._is_quota_window_expired,
                )
                next_snapshot = persist_consumed_usage(
                    conn,
                    identity=identity,
                    snapshot=snapshot,
                    consumed_units=consumed_units,
                    now_provider=self._service.now_provider,
                    execute=self._service._execute,
                )
                conn.commit()
            return compute_remaining_quota(
                used_count=next_snapshot.used_count,
                quota_limit=identity.quota_limit,
            )

    def _ensure_and_lock_postgres_usage(self, conn, *, identity: IdentityContext) -> None:
        now = self._service.now_provider().isoformat()
        cursor = self._service._execute(
            conn,
            """
            INSERT INTO usage (
                identity_type, identity_id, used_count, quota_limit, updated_at, window_started_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, identity_id) DO NOTHING
            """,
            (identity.identity_type, identity.identity_id, 0, identity.quota_limit, now, now),
        )
        cursor.close()
        cursor = self._service._execute(
            conn,
            """
            SELECT identity_type
            FROM usage
            WHERE identity_type = ? AND identity_id = ?
            FOR UPDATE
            """,
            (identity.identity_type, identity.identity_id),
        )
        try:
            cursor.fetchone()
        finally:
            cursor.close()

    def _reserve_consumption(
        self,
        conn,
        *,
        identity: IdentityContext,
        idempotency_key: str,
        consumed_units: int,
    ) -> bool:
        cursor = self._service._execute(
            conn,
            """
            INSERT INTO conversion_quota_consumptions (
                identity_type,
                identity_id,
                idempotency_key,
                consumed_units,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, identity_id, idempotency_key) DO NOTHING
            """,
            (
                identity.identity_type,
                identity.identity_id,
                idempotency_key,
                max(1, int(consumed_units)),
                self._service.now_provider().isoformat(),
            ),
        )
        try:
            return int(cursor.rowcount or 0) == 1
        finally:
            close = getattr(cursor, "close", None)
            if self._service._use_postgres and callable(close):
                close()

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        usage = self._service._read_usage(identity)
        return compute_remaining_quota(
            used_count=int(usage["used_count"]),
            quota_limit=identity.quota_limit,
        )

    def get_quota_reset_at(self, identity: IdentityContext) -> str:
        usage = self._service._read_usage(identity)
        if identity.quota_anchor_at:
            return monthly_window(datetime.fromisoformat(identity.quota_anchor_at), self._service.now_provider())[1].isoformat()
        return compute_quota_reset_at(
            window_started_at=usage["window_started_at"],
            quota_window_days=identity.quota_window_days,
        )
