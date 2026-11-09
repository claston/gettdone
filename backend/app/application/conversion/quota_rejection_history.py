"""Persist quota refusals that happen before an asynchronous job exists."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from app.application.conversion.contracts.access import ConversionAccessPort
from app.application.conversion.identity import IdentityContext

if TYPE_CHECKING:
    from app.application.conversion.conversion_batch_service import ConversionBatchFile

logger = logging.getLogger(__name__)


def record_batch_quota_rejection(
    *,
    access_control_service: ConversionAccessPort,
    identity: IdentityContext,
    files: Sequence[ConversionBatchFile],
    idempotency_key: str,
    quota_code: str,
    ocr_mode: str,
    exception_class: str | None = None,
) -> None:
    for index, file in enumerate(files):
        # Scope retries to the owner, request and file; never collide with worker history IDs.
        key = json.dumps([
            "batch-quota-v1", identity.identity_type, identity.identity_id, idempotency_key.strip(),
            ocr_mode, index, file.filename, file.content_type, file.size_bytes, file.sha256_hex.lower(),
        ], ensure_ascii=True)
        event_id = "quota_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        extension = Path(file.filename).suffix.lower().strip(".") or "pdf"
        details = {
            "filename": file.filename,
            "model": "Nao identificado",
            "conversion_type": f"{extension}-conversion",
            "status": "Falha",
            "transactions_count": None if identity.identity_type == "anonymous" else 0,
            "pages_count": None,
            "scanned_likely": None,
            "ocr_used": False,
            "ocr_pages_processed": 0,
            "duration_ms": 0,
            "error_code": "quota_exceeded",
            "error_stage": "batch_preflight",
            "error_subcode": quota_code,
            "exception_class": exception_class,
            # Upload never happened: this is the client-declared hash, not a verified digest.
            "file_sha256": file.sha256_hex.lower(),
        }
        try:
            if identity.identity_type == "user":
                access_control_service.record_user_conversion(user_id=identity.identity_id, processing_id=event_id, **details)
            else:
                access_control_service.record_anonymous_conversion_event(
                    anonymous_fingerprint=identity.identity_id, event_id=event_id, **details,
                )
        except Exception as exc:
            # Observability must not change the quota decision or expose document/provider details.
            logger.warning("batch_quota_history_persist_failed event_id=%s error_class=%s", event_id, type(exc).__name__)
