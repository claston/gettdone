from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.application.conversion.uploaded_document import (
    UploadedDocument,
    UploadedDocumentStage,
    ingest_uploaded_document,
)

_STORAGE_KEY_PATTERN = re.compile(r"^doc_[a-f0-9]{24}$")


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


class FilesystemConversionDocumentStore:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def store(self, document: UploadedDocument) -> ConversionDocumentReference:
        storage_key = f"doc_{uuid4().hex[:24]}"
        reference = ConversionDocumentReference.from_document(document, storage_key=storage_key)
        target_path = self._resolve_document_path(reference)
        target_path.parent.mkdir(parents=True, exist_ok=False)
        temporary_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
        try:
            temporary_path.write_bytes(document.raw_bytes)
            temporary_path.replace(target_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            target_path.unlink(missing_ok=True)
            try:
                target_path.parent.rmdir()
            except OSError:
                pass
            raise
        return reference

    def load(self, reference: ConversionDocumentReference) -> UploadedDocument:
        target_path = self._resolve_document_path(reference)
        raw_bytes = target_path.read_bytes()
        if len(raw_bytes) != reference.size_bytes:
            raise ValueError("Stored conversion document size does not match its reference.")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        if digest != reference.sha256_hex:
            raise ValueError("Stored conversion document digest does not match its reference.")
        return ingest_uploaded_document(
            filename=reference.filename,
            raw_bytes=raw_bytes,
            staging=UploadedDocumentStage(
                path=target_path,
                size_bytes=reference.size_bytes,
                sha256_hex=reference.sha256_hex,
            ),
        )

    def delete(self, reference: ConversionDocumentReference) -> None:
        target_path = self._resolve_document_path(reference)
        target_path.unlink(missing_ok=True)
        try:
            target_path.parent.rmdir()
        except OSError:
            pass

    def _resolve_document_path(self, reference: ConversionDocumentReference) -> Path:
        if _STORAGE_KEY_PATTERN.fullmatch(reference.storage_key) is None:
            raise ValueError("Invalid conversion document storage key.")
        if not reference.file_type or re.fullmatch(r"[a-z0-9]{1,10}", reference.file_type) is None:
            raise ValueError("Invalid conversion document file type.")
        target_path = (self.root_dir / reference.storage_key / f"document.{reference.file_type}").resolve()
        if self.root_dir not in target_path.parents:
            raise ValueError("Conversion document path escapes the configured store root.")
        return target_path
