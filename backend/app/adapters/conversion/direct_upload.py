from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.uploads import PreparedS3Upload, canonical_document_content_type

_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


_STORAGE_KEY_PATTERN = re.compile(r"^doc_[a-f0-9]{24}$")


_CONTENT_TYPES = {
    "csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
    "ofx": {"application/x-ofx", "application/ofx", "text/plain", "application/octet-stream"},
    "pdf": {"application/pdf"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}


class S3DirectUploadService:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "conversion/jobs",
        region: str | None = None,
        expires_in_seconds: int = 900,
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
        s3_client: Any | None = None,
        storage_key_factory: Callable[[], str] | None = None,
    ) -> None:
        self.bucket = (bucket or "").strip()
        if not self.bucket:
            raise ValueError("S3 direct upload bucket is required.")
        self.prefix = (prefix or "").strip().strip("/")
        self.region = (region or "").strip() or None
        self.expires_in_seconds = max(60, min(int(expires_in_seconds), 3600))
        self.server_side_encryption = (server_side_encryption or "").strip()
        self.kms_key_id = (kms_key_id or "").strip() or None
        if self.kms_key_id and self.server_side_encryption != "aws:kms":
            raise ValueError("An S3 KMS key requires server-side encryption set to aws:kms.")
        self._s3_client = s3_client
        self.storage_key_factory = storage_key_factory or (lambda: f"doc_{uuid4().hex[:24]}")

    def prepare(
        self,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256_hex: str,
        max_size_bytes: int,
    ) -> PreparedS3Upload:
        safe_filename = self._safe_filename(filename)
        file_type = PurePosixPath(safe_filename).suffix.lower().lstrip(".")
        if file_type not in _CONTENT_TYPES:
            raise ValueError("Unsupported conversion document type.")

        normalized_content_type = (content_type or "").strip().lower()
        if normalized_content_type not in _CONTENT_TYPES[file_type]:
            raise ValueError("Invalid content type for conversion document.")
        if not 1 <= int(size_bytes) <= int(max_size_bytes):
            raise ValueError("Conversion document size is outside the permitted range.")

        normalized_digest = (sha256_hex or "").strip().lower()
        if _SHA256_PATTERN.fullmatch(normalized_digest) is None:
            raise ValueError("A valid SHA-256 digest is required.")

        storage_key = self.storage_key_factory()
        if _STORAGE_KEY_PATTERN.fullmatch(storage_key) is None:
            raise ValueError("Invalid direct upload storage key.")
        document = ConversionDocumentReference(
            storage_key=storage_key,
            filename=safe_filename,
            file_type=file_type,
            size_bytes=int(size_bytes),
            sha256_hex=normalized_digest,
        )
        return self.prepare_reference(
            document=document,
            content_type=normalized_content_type,
            max_size_bytes=max_size_bytes,
        )

    def prepare_reference(
        self,
        *,
        document: ConversionDocumentReference,
        content_type: str,
        max_size_bytes: int,
    ) -> PreparedS3Upload:
        normalized_content_type = (content_type or "").strip().lower()
        supported_content_types = _CONTENT_TYPES.get(document.file_type)
        if supported_content_types is None or normalized_content_type not in supported_content_types:
            raise ValueError("Invalid content type for conversion document.")
        if not 1 <= document.size_bytes <= int(max_size_bytes):
            raise ValueError("Conversion document size is outside the permitted range.")
        if _SHA256_PATTERN.fullmatch(document.sha256_hex or "") is None:
            raise ValueError("A valid SHA-256 digest is required.")

        object_key = self.object_key(document)
        fields = {
            "Content-Type": normalized_content_type,
            "x-amz-meta-sha256": document.sha256_hex,
        }
        conditions: list[object] = [
            ["content-length-range", 1, int(max_size_bytes)],
            {"Content-Type": normalized_content_type},
            {"x-amz-meta-sha256": document.sha256_hex},
        ]
        if self.server_side_encryption:
            fields["x-amz-server-side-encryption"] = self.server_side_encryption
            conditions.append({"x-amz-server-side-encryption": self.server_side_encryption})
        if self.kms_key_id:
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = self.kms_key_id
            conditions.append({"x-amz-server-side-encryption-aws-kms-key-id": self.kms_key_id})

        response = self._client().generate_presigned_post(
            Bucket=self.bucket,
            Key=object_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=self.expires_in_seconds,
        )
        return PreparedS3Upload(
            document=document,
            object_key=object_key,
            upload_url=str(response["url"]),
            upload_fields={str(key): str(value) for key, value in response["fields"].items()},
            expires_in_seconds=self.expires_in_seconds,
        )

    def verify_uploaded(self, document: ConversionDocumentReference) -> None:
        response = self._client().head_object(Bucket=self.bucket, Key=self.object_key(document))
        if int(response.get("ContentLength", -1)) != document.size_bytes:
            raise ValueError("Uploaded conversion document size does not match its declaration.")
        metadata = response.get("Metadata") or {}
        uploaded_digest = str(metadata.get("sha256") or "").strip().lower()
        if uploaded_digest != document.sha256_hex:
            raise ValueError("Uploaded conversion document SHA-256 metadata does not match its declaration.")

    def object_key(self, document: ConversionDocumentReference) -> str:
        relative = f"{document.storage_key}/document.{document.file_type}"
        return f"{self.prefix}/{relative}" if self.prefix else relative

    @staticmethod
    def canonical_content_type(file_type: str) -> str:
        return canonical_document_content_type(file_type)

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - installed in production
                raise RuntimeError("S3 direct uploads require boto3.") from exc
            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client

    @staticmethod
    def _safe_filename(filename: str) -> str:
        normalized = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not normalized or len(normalized) > 255:
            raise ValueError("Invalid conversion document filename.")
        return normalized
