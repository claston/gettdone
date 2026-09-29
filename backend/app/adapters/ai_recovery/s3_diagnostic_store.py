from __future__ import annotations

import json
from typing import Any

from app.application.ai_recovery.privacy_fixture import PrivacySafeFixture
from app.application.ai_recovery.schemas import AIRecoveryRequestManifest


class S3AIRecoveryObjectReader:
    def __init__(self, *, region: str | None = None, s3_client: Any | None = None) -> None:
        self.region = str(region or "").strip() or None
        self._s3_client = s3_client

    def get_bytes(self, *, bucket: str, key: str) -> bytes:
        response = self._client().get_object(Bucket=bucket, Key=key)
        return bytes(response["Body"].read())

    def _client(self):
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client


class S3AIRecoveryResultStore:
    def __init__(
        self,
        *,
        bucket: str,
        restricted_prefix: str = "ai-recovery/results/restricted/v1",
        fixture_prefix: str = "ai-recovery/fixtures/v1",
        region: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = str(bucket or "").strip()
        if not self.bucket:
            raise ValueError("AI recovery result bucket is required.")
        self.restricted_prefix = restricted_prefix.strip().strip("/")
        self.fixture_prefix = fixture_prefix.strip().strip("/")
        self.region = str(region or "").strip() or None
        self._s3_client = s3_client

    def store_restricted(
        self,
        *,
        manifest: AIRecoveryRequestManifest,
        diagnostic: dict[str, object],
    ) -> None:
        base = self._base(self.restricted_prefix, manifest)
        self._put_json(f"{base}/diagnostic.json", diagnostic, classification="restricted")

    def restricted_exists(self, *, manifest: AIRecoveryRequestManifest) -> bool:
        key = f"{self._base(self.restricted_prefix, manifest)}/diagnostic.json"
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def store_fixture(self, *, manifest: AIRecoveryRequestManifest, fixture: PrivacySafeFixture) -> None:
        if fixture.manifest.get("privacy_validated") is not True:
            raise ValueError("Refusing to publish an AI recovery fixture without privacy validation.")
        base = self._base(self.fixture_prefix, manifest)
        self._put(f"{base}/input.pdf", fixture.pdf_bytes, "application/pdf", classification="shareable-fixture")
        self._put_json(f"{base}/expected.json", fixture.expected, classification="shareable-fixture")
        self._put(
            f"{base}/README.md",
            fixture.readme.encode("utf-8"),
            "text/markdown; charset=utf-8",
            classification="shareable-fixture",
        )
        # The validated manifest is the discovery marker and must be visible last.
        self._put_json(f"{base}/manifest.json", fixture.manifest, classification="shareable-fixture")

    @staticmethod
    def _base(prefix: str, manifest: AIRecoveryRequestManifest) -> str:
        partition = manifest.created_at.date().isoformat()
        return f"{prefix}/{partition}/{manifest.idempotency_key}"

    def _put_json(self, key: str, value: dict[str, object], *, classification: str) -> None:
        self._put(
            key,
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"),
            "application/json",
            classification=classification,
        )

    def _put(self, key: str, body: bytes, content_type: str, *, classification: str) -> None:
        self._client().put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentLength=len(body),
            ContentType=content_type,
            ServerSideEncryption="AES256",
            Metadata={"classification": classification},
            IfNoneMatch="*",
        )

    def _client(self):
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    metadata = response.get("ResponseMetadata")
    code = str(error.get("Code") or "") if isinstance(error, dict) else ""
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404
