from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.document_preflight_service import DocumentPreflightResult

OcrProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ConversionJob:
    """Immutable application command consumed by a conversion executor.

    Runtime callbacks intentionally live in ``ConversionExecutionHooks`` so a
    future queued executor can map job data to its transport without carrying
    process-local callables.
    """

    job_id: str
    document: ConversionDocumentReference
    identity: IdentityContext
    preflight_result: DocumentPreflightResult

    @classmethod
    def create(
        cls,
        *,
        document: ConversionDocumentReference,
        identity: IdentityContext,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
        job_id: str | None = None,
    ) -> ConversionJob:
        return cls(
            job_id=(job_id or "").strip() or f"job_{uuid4().hex[:24]}",
            document=document,
            identity=identity,
            preflight_result=DocumentPreflightResult(
                scanned_likely=bool(scanned_likely),
                estimated_pages_count=estimated_pages_count,
            ),
        )


@dataclass(frozen=True, slots=True)
class ConversionExecutionHooks:
    on_ocr_progress: OcrProgressCallback | None = None
