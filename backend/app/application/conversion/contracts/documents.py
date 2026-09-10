from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.uploaded_document import (
    UploadedDocument,
)


@dataclass(frozen=True, slots=True)
class ConversionDocumentReference:
    storage_key: str
    filename: str
    file_type: str
    size_bytes: int
    sha256_hex: str

    @classmethod
    def from_document(cls, document: UploadedDocument, *, storage_key: str) -> ConversionDocumentReference:
        digest = hashlib.sha256(document.raw_bytes).hexdigest()
        return cls(
            storage_key=storage_key,
            filename=document.filename,
            file_type=document.file_type,
            size_bytes=document.size_bytes,
            sha256_hex=digest,
        )


class ConversionDocumentStore(Protocol):
    def store(self, document: UploadedDocument) -> ConversionDocumentReference: ...

    def load(self, reference: ConversionDocumentReference) -> UploadedDocument: ...

    def delete(self, reference: ConversionDocumentReference) -> None: ...
