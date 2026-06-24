from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.application.errors import UnsupportedFileTypeError

SUPPORTED_DOCUMENT_EXTENSIONS = {"csv", "xlsx", "ofx", "pdf"}


@dataclass(frozen=True)
class UploadedDocumentStage:
    path: Path
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True)
class UploadedDocument:
    filename: str
    file_type: str
    raw_bytes: bytes
    staging: UploadedDocumentStage | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.raw_bytes)

    @classmethod
    def from_staged_upload(cls, *, filename: str, staged_upload: UploadedDocumentStage) -> UploadedDocument:
        return ingest_uploaded_document(
            filename=filename,
            raw_bytes=staged_upload.path.read_bytes(),
            staging=staged_upload,
        )


def ingest_uploaded_document(
    filename: str,
    raw_bytes: bytes,
    *,
    staging: UploadedDocumentStage | None = None,
) -> UploadedDocument:
    file_type = Path(filename or "").suffix.replace(".", "").lower()
    if file_type not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise UnsupportedFileTypeError
    return UploadedDocument(
        filename=filename,
        file_type=file_type,
        raw_bytes=raw_bytes,
        staging=staging,
    )
