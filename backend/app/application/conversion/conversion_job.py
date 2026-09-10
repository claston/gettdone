from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.preflight import DocumentPreflightResult
from app.application.conversion.identity import IdentityContext

OcrProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ConversionJob:
    """Immutable application command consumed by a conversion executor.

    Runtime callbacks intentionally live in ``ConversionExecutionHooks`` so a
    future queued executor can map job data to its transport without carrying
    process-local callables.
    """

    job_id: str
    idempotency_key: str
    document: ConversionDocumentReference
    identity: IdentityContext
    preflight_result: DocumentPreflightResult
    batch_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        document: ConversionDocumentReference,
        identity: IdentityContext,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
        job_id: str | None = None,
        idempotency_key: str | None = None,
        batch_id: str | None = None,
    ) -> ConversionJob:
        resolved_job_id = (job_id or "").strip() or f"job_{uuid4().hex[:24]}"
        return cls(
            job_id=resolved_job_id,
            idempotency_key=(idempotency_key or "").strip() or resolved_job_id,
            document=document,
            identity=identity,
            preflight_result=DocumentPreflightResult(
                scanned_likely=None if scanned_likely is None else bool(scanned_likely),
                estimated_pages_count=estimated_pages_count,
            ),
            batch_id=(batch_id or "").strip() or None,
        )


@dataclass(frozen=True, slots=True)
class ConversionExecutionHooks:
    on_ocr_progress: OcrProgressCallback | None = None
