from __future__ import annotations

from typing import TYPE_CHECKING

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

    def consume_quota(self, identity: IdentityContext, *, consumed_units: int = 1) -> int:
        with self._service._lock:
            with self._service._connect() as conn:
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

    def get_remaining_quota(self, identity: IdentityContext) -> int:
        usage = self._service._read_usage(identity)
        return compute_remaining_quota(
            used_count=int(usage["used_count"]),
            quota_limit=identity.quota_limit,
        )

    def get_quota_reset_at(self, identity: IdentityContext) -> str:
        usage = self._service._read_usage(identity)
        return compute_quota_reset_at(
            window_started_at=usage["window_started_at"],
            quota_window_days=identity.quota_window_days,
        )
