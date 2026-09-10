from __future__ import annotations

from typing import Any, Protocol

from app.application.conversion.identity import IdentityContext


class ConversionAccessPort(Protocol):
    """Operations the shared conversion core may perform on access data."""

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int) -> None: ...

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None: ...

    def consume_quota(
        self,
        identity: IdentityContext,
        *,
        consumed_units: int = 1,
        idempotency_key: str | None = None,
    ) -> int: ...

    def record_user_conversion(self, **kwargs: Any) -> None: ...

    def record_anonymous_conversion_event(self, **kwargs: Any) -> None: ...
