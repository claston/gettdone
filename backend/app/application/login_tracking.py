from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService

logger = logging.getLogger(__name__)


def record_successful_login_safely(
    service: AccessControlService,
    *,
    user_id: str,
    auth_method: str,
) -> None:
    """Persist login analytics without making authentication depend on analytics."""
    try:
        event_id = service.record_successful_login(
            user_id=user_id,
            auth_method=auth_method,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "user_login_tracking_failed user_id=%s auth_method=%s",
            user_id,
            auth_method,
        )
        return

    logger.info(
        "user_login_recorded event_id=%s user_id=%s auth_method=%s",
        event_id,
        user_id,
        auth_method,
    )
