from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.application.conversion.document_preflight_service import DocumentPreflightResult
from app.application.conversion.uploaded_document import UploadedDocument

OcrProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ConversionJob:
    """Immutable application command consumed by a conversion executor.

    Runtime callbacks intentionally live in ``ConversionExecutionHooks`` so a
    future queued executor can map job data to its transport without carrying
    process-local callables.
    """

    document: UploadedDocument
    anonymous_fingerprint: str | None
    user_token: str | None
    authorization: str | None
    access_cookie_token: str | None
    preflight_result: DocumentPreflightResult

    @classmethod
    def from_inputs(
        cls,
        *,
        document: UploadedDocument,
        anonymous_fingerprint: str | None,
        user_token: str | None,
        authorization: str | None,
        access_cookie_token: str | None,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
    ) -> ConversionJob:
        return cls(
            document=document,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            preflight_result=DocumentPreflightResult(
                scanned_likely=bool(scanned_likely),
                estimated_pages_count=estimated_pages_count,
            ),
        )


@dataclass(frozen=True, slots=True)
class ConversionExecutionHooks:
    on_ocr_progress: OcrProgressCallback | None = None
