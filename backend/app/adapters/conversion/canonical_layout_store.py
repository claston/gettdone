from __future__ import annotations

import json
import re
from typing import Any

from app.application.conversion.canonical_layout_capture import (
    CanonicalLayoutArtifact,
    CanonicalLayoutCaptureService,
    CanonicalLayoutGenerator,
)

_CAPTURE_ID_PATTERN = re.compile(r"^cap_[a-f0-9]{24}$")


class S3CanonicalLayoutStore:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "conversion/canonical-layouts/candidates/v1",
        region: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = str(bucket or "").strip()
        if not self.bucket:
            raise ValueError("Canonical layout S3 bucket is required.")
        self.prefix = str(prefix or "").strip().strip("/")
        self.region = str(region or "").strip() or None
        self._s3_client = s3_client

    def store(self, artifact: CanonicalLayoutArtifact) -> None:
        if not artifact.privacy_validated:
            raise ValueError("Canonical artifact did not pass privacy validation.")
        if _CAPTURE_ID_PATTERN.fullmatch(artifact.capture_id) is None:
            raise ValueError("Invalid canonical capture id.")
        base_key = f"{self.prefix}/{artifact.capture_id}" if self.prefix else artifact.capture_id
        metadata = {
            "schema-version": str(artifact.manifest.get("schema_version") or "unknown")[:20],
            "privacy-validation-version": str(
                artifact.manifest.get("privacy_validation_version") or "unknown"
            )[:20],
        }
        self._client().put_object(
            Bucket=self.bucket,
            Key=f"{base_key}/canonical.pdf",
            Body=artifact.pdf_bytes,
            ContentLength=len(artifact.pdf_bytes),
            ContentType="application/pdf",
            Metadata=metadata,
            ServerSideEncryption="AES256",
        )
        manifest_bytes = json.dumps(
            artifact.manifest,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._client().put_object(
            Bucket=self.bucket,
            Key=f"{base_key}/layout.json",
            Body=manifest_bytes,
            ContentLength=len(manifest_bytes),
            ContentType="application/json",
            Metadata=metadata,
            ServerSideEncryption="AES256",
        )

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - production dependency
                raise RuntimeError("Canonical layout S3 storage requires boto3.") from exc
            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client


def build_s3_canonical_layout_capture_service(
    *,
    enabled: bool,
    bucket: str,
    prefix: str = "conversion/canonical-layouts/candidates/v1",
    region: str | None = None,
    max_pages: int = 20,
    max_extracted_chars: int = 250_000,
) -> CanonicalLayoutCaptureService:
    if not enabled:
        return CanonicalLayoutCaptureService(enabled=False)
    return CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(
            max_pages=max_pages,
            max_extracted_chars=max_extracted_chars,
        ),
        store=S3CanonicalLayoutStore(
            bucket=bucket,
            prefix=prefix,
            region=region,
        ),
    )
