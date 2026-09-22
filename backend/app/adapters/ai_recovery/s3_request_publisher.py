from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.ai_recovery.request_publishing import (
    DEFAULT_AI_RECOVERY_REQUEST_PREFIX,
    AIRecoveryObjectCollisionError,
    AIRecoveryPublication,
    AIRecoveryRequestArtifacts,
    normalize_ai_recovery_request_prefix,
    validate_ai_recovery_request_artifacts,
)


class S3AIRecoveryRequestPublisher:
    """Publish immutable recovery inputs, exposing the ready manifest last."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = DEFAULT_AI_RECOVERY_REQUEST_PREFIX,
        region: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = str(bucket or "").strip()
        if not self.bucket:
            raise ValueError("AI recovery S3 bucket is required.")
        self.prefix = normalize_ai_recovery_request_prefix(prefix)
        self.region = str(region or "").strip() or None
        self._s3_client = s3_client

    def publish(self, artifacts: AIRecoveryRequestArtifacts) -> AIRecoveryPublication:
        keys = validate_ai_recovery_request_artifacts(
            artifacts,
            bucket=self.bucket,
            prefix=self.prefix,
        )
        manifest_bytes = json.dumps(
            artifacts.manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        objects = (
            (keys.input_pdf, artifacts.pdf_bytes, "application/pdf", "input"),
            (keys.deterministic_artifact, artifacts.deterministic_artifact, "application/json", "deterministic"),
            (keys.source_evidence, artifacts.source_evidence, "application/json", "source-evidence"),
            (keys.ready, manifest_bytes, "application/json", "ready"),
        )
        created_keys: list[str] = []
        reused_keys: list[str] = []
        for key, body, content_type, artifact_type in objects:
            created = self._put_immutable_object(
                key=key,
                body=body,
                content_type=content_type,
                artifact_type=artifact_type,
                idempotency_key=artifacts.manifest.idempotency_key,
            )
            (created_keys if created else reused_keys).append(key)
        return AIRecoveryPublication(
            ready_key=keys.ready,
            created_keys=tuple(created_keys),
            reused_keys=tuple(reused_keys),
        )

    def _put_immutable_object(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
        artifact_type: str,
        idempotency_key: str,
    ) -> bool:
        sha256 = hashlib.sha256(body).hexdigest()
        metadata = {
            "sha256": sha256,
            "artifact-type": artifact_type,
            "idempotency-key": idempotency_key,
        }
        try:
            self._client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentLength=len(body),
                ContentType=content_type,
                Metadata=metadata,
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
            )
            return True
        except Exception as exc:
            if not _is_precondition_failed(exc):
                raise

        existing = self._client().head_object(Bucket=self.bucket, Key=key)
        existing_metadata = existing.get("Metadata") or {}
        same_content = (
            existing.get("ContentLength") == len(body)
            and existing.get("ContentType") == content_type
            and existing_metadata.get("sha256") == sha256
            and existing_metadata.get("artifact-type") == artifact_type
            and existing_metadata.get("idempotency-key") == idempotency_key
            and existing.get("ServerSideEncryption") == "AES256"
        )
        if not same_content:
            raise AIRecoveryObjectCollisionError(
                f"AI recovery existing S3 object does not match immutable content: {key}"
            )
        return False

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - production dependency
                raise RuntimeError("AI recovery S3 publishing requires boto3.") from exc
            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client


def _is_precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    response_metadata = response.get("ResponseMetadata")
    code = error.get("Code") if isinstance(error, dict) else None
    status = response_metadata.get("HTTPStatusCode") if isinstance(response_metadata, dict) else None
    return code in {"PreconditionFailed", "412"} or status == 412
