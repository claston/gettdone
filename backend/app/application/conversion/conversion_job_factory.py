from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.contracts.documents import ConversionDocumentStore
from app.application.conversion.contracts.jobs import ConversionJobRepository
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_job_cleanup_service import ConversionJobCleanupService
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.errors import InvalidSessionTokenError, InvalidUserTokenError


class ConversionIdentityResolver(Protocol):
    def get_user_by_session_access_token(self, token: str): ...

    def resolve_identity(self, anonymous_fingerprint: str | None, user_token: str | None): ...


@dataclass(frozen=True, slots=True)
class ConversionJobFactory:
    access_control_service: ConversionIdentityResolver
    document_store: ConversionDocumentStore
    job_repository: ConversionJobRepository
    cleanup_service: ConversionJobCleanupService | None = None

    def create(
        self,
        *,
        document: UploadedDocument,
        anonymous_fingerprint: str | None,
        user_token: str | None,
        authorization: str | None,
        access_cookie_token: str | None,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
        idempotency_key: str | None = None,
    ) -> ConversionJob:
        identity = resolve_conversion_identity(
            access_control_service=self.access_control_service,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
        )
        if self.cleanup_service is not None:
            self.cleanup_service.cleanup_expired()
        document_reference = self.document_store.store(document)
        try:
            job = ConversionJob.create(
                document=document_reference,
                identity=identity,
                scanned_likely=scanned_likely,
                estimated_pages_count=estimated_pages_count,
                idempotency_key=idempotency_key,
            )
            submission = self.job_repository.submit(job)
            if not submission.created:
                self.document_store.delete(document_reference)
            return submission.record.job
        except Exception:
            self.document_store.delete(document_reference)
            raise


def resolve_conversion_identity(
    *,
    access_control_service: ConversionIdentityResolver,
    anonymous_fingerprint: str | None,
    user_token: str | None,
    authorization: str | None,
    access_cookie_token: str | None,
):
    resolved_token = _resolve_header_or_query_token(
        authorization=authorization,
        query_token=user_token,
    )
    if not resolved_token:
        cookie_token = (access_cookie_token or "").strip()
        if cookie_token:
            try:
                resolved_token = access_control_service.get_user_by_session_access_token(cookie_token).token
            except InvalidSessionTokenError:
                raise InvalidUserTokenError from None
    return access_control_service.resolve_identity(
        anonymous_fingerprint=anonymous_fingerprint,
        user_token=resolved_token,
    )


def _resolve_header_or_query_token(*, authorization: str | None, query_token: str | None) -> str:
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer
    return (query_token or "").strip()
