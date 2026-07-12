from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.application.conversion.conversion_document_store import ConversionDocumentStore
from app.application.conversion.conversion_job_repository import ConversionJobRepository


@dataclass(frozen=True, slots=True)
class ConversionJobCleanupService:
    job_repository: ConversionJobRepository
    document_store: ConversionDocumentStore

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        removed = 0
        for record in self.job_repository.list_expired(now=now):
            self.document_store.delete(record.job.document)
            self.job_repository.delete(record.job.job_id)
            removed += 1
        return removed
