from dataclasses import dataclass
from pathlib import Path

from app.application.errors import UnsupportedFileTypeError

SUPPORTED_DOCUMENT_EXTENSIONS = {"csv", "xlsx", "ofx", "pdf"}


@dataclass(frozen=True)
class IngestedDocument:
    filename: str
    file_type: str
    raw_bytes: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.raw_bytes)


def ingest_uploaded_document(filename: str, raw_bytes: bytes) -> IngestedDocument:
    file_type = Path(filename or "").suffix.replace(".", "").lower()
    if file_type not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise UnsupportedFileTypeError
    return IngestedDocument(
        filename=filename,
        file_type=file_type,
        raw_bytes=raw_bytes,
    )
