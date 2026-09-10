from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.uploaded_document import (
    UploadedDocument,
    UploadedDocumentStage,
    ingest_uploaded_document,
)

_STORAGE_KEY_PATTERN = re.compile(r"^doc_[a-f0-9]{24}$")


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
        _validate_document_reference(reference)
        target_path = (self.root_dir / reference.storage_key / f"document.{reference.file_type}").resolve()
        if self.root_dir not in target_path.parents:
            raise ValueError("Conversion document path escapes the configured store root.")
        return target_path


class S3ConversionDocumentStore:
    """Private S3-backed document store shared by the API and future workers."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "conversion/jobs",
        region: str | None = None,
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = (bucket or "").strip()
        if not self.bucket:
            raise ValueError("S3 conversion document bucket is required.")
        self.prefix = (prefix or "").strip().strip("/")
        self.region = (region or "").strip() or None
        self.server_side_encryption = (server_side_encryption or "").strip()
        self.kms_key_id = (kms_key_id or "").strip() or None
        if self.kms_key_id and self.server_side_encryption != "aws:kms":
            raise ValueError("An S3 KMS key requires server-side encryption set to aws:kms.")
        self._s3_client = s3_client

    def store(self, document: UploadedDocument) -> ConversionDocumentReference:
        storage_key = f"doc_{uuid4().hex[:24]}"
        reference = ConversionDocumentReference.from_document(document, storage_key=storage_key)
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(reference),
            "Body": document.raw_bytes,
            "ContentLength": document.size_bytes,
            "ContentType": _content_type(reference.file_type),
            "Metadata": {"sha256": reference.sha256_hex},
        }
        if self.server_side_encryption:
            params["ServerSideEncryption"] = self.server_side_encryption
        if self.kms_key_id:
            params["SSEKMSKeyId"] = self.kms_key_id
        self._client().put_object(**params)
        return reference

    def load(self, reference: ConversionDocumentReference) -> UploadedDocument:
        response = self._client().get_object(Bucket=self.bucket, Key=self._object_key(reference))
        body = response["Body"]
        try:
            raw_bytes = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if len(raw_bytes) != reference.size_bytes:
            raise ValueError("Stored conversion document size does not match its reference.")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        if digest != reference.sha256_hex:
            raise ValueError("Stored conversion document digest does not match its reference.")
        return ingest_uploaded_document(filename=reference.filename, raw_bytes=raw_bytes)

    def delete(self, reference: ConversionDocumentReference) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=self._object_key(reference))

    def _object_key(self, reference: ConversionDocumentReference) -> str:
        _validate_document_reference(reference)
        relative_key = f"{reference.storage_key}/document.{reference.file_type}"
        if not self.prefix:
            return relative_key
        return f"{self.prefix}/{relative_key}"

    def _client(self):
        if self._s3_client is None:
            boto3 = _load_boto3()
            session = boto3.session.Session(region_name=self.region)
            self._s3_client = session.client("s3")
        return self._s3_client


def _validate_document_reference(reference: ConversionDocumentReference) -> None:
    if _STORAGE_KEY_PATTERN.fullmatch(reference.storage_key) is None:
        raise ValueError("Invalid conversion document storage key.")
    if not reference.file_type or re.fullmatch(r"[a-z0-9]{1,10}", reference.file_type) is None:
        raise ValueError("Invalid conversion document file type.")


def _content_type(file_type: str) -> str:
    return {
        "csv": "text/csv",
        "ofx": "application/x-ofx",
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(file_type, "application/octet-stream")


def _load_boto3():
    try:
        import boto3
    except Exception as exc:  # pragma: no cover - dependency is part of production requirements
        raise RuntimeError("S3 conversion storage requires boto3.") from exc
    return boto3
