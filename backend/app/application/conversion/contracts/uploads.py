from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.contracts.documents import ConversionDocumentReference


@dataclass(frozen=True, slots=True)
class PreparedS3Upload:
    document: ConversionDocumentReference
    object_key: str
    upload_url: str
    upload_fields: dict[str, str]
    expires_in_seconds: int


class DirectUploadService(Protocol):
    def prepare(
        self,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256_hex: str,
        max_size_bytes: int,
    ) -> PreparedS3Upload: ...

    def prepare_reference(
        self,
        *,
        document: ConversionDocumentReference,
        content_type: str,
        max_size_bytes: int,
    ) -> PreparedS3Upload: ...

    def verify_uploaded(self, document: ConversionDocumentReference) -> None: ...


class QueuePublisher(Protocol):
    def publish(self, *, job_id: str, batch_id: str, trace_id: str) -> str: ...


def canonical_document_content_type(file_type: str) -> str:
    return {
        "csv": "text/csv",
        "ofx": "application/x-ofx",
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[file_type]
