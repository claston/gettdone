from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from app.application.ai_recovery.schemas import (
    AIRecoveryDocumentReference,
    AIRecoveryRequestManifest,
    AIRecoveryVersionSet,
    DeterministicArtifactReference,
)

DEFAULT_AI_RECOVERY_REQUEST_PREFIX = "ai-recovery/requests/v1"


class AIRecoveryRequestIntegrityError(ValueError):
    """The request artifacts do not match their immutable manifest."""


class AIRecoveryObjectCollisionError(RuntimeError):
    """An immutable object key is already occupied by different content."""


@dataclass(frozen=True, slots=True)
class AIRecoveryObjectKeys:
    input_pdf: str
    deterministic_artifact: str
    source_evidence: str
    ready: str


@dataclass(frozen=True, slots=True)
class AIRecoveryRequestArtifacts:
    manifest: AIRecoveryRequestManifest
    pdf_bytes: bytes
    deterministic_artifact: bytes
    source_evidence: bytes


@dataclass(frozen=True, slots=True)
class AIRecoveryPublication:
    ready_key: str
    created_keys: tuple[str, ...]
    reused_keys: tuple[str, ...]


def build_ai_recovery_idempotency_key(
    *,
    analysis_id: str,
    document_sha256: str,
    parser_release: str,
    prompt_version: str,
    output_schema_version: str,
    comparator_version: str,
    model_id: str,
) -> str:
    components = (
        analysis_id,
        document_sha256,
        parser_release,
        prompt_version,
        output_schema_version,
        comparator_version,
        model_id,
    )
    payload = "ai-recovery:v1\0" + "\0".join(components)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_ai_recovery_object_keys(
    *,
    prefix: str,
    created_at: datetime,
    idempotency_key: str,
) -> AIRecoveryObjectKeys:
    normalized_prefix = normalize_ai_recovery_request_prefix(prefix)
    _require_timezone(created_at, field_name="created_at")
    date_partition = created_at.astimezone(UTC).date().isoformat()
    base_key = f"{normalized_prefix}/{date_partition}/{idempotency_key}"
    return AIRecoveryObjectKeys(
        input_pdf=f"{base_key}/input.pdf",
        deterministic_artifact=f"{base_key}/deterministic.json",
        source_evidence=f"{base_key}/source-evidence.json",
        ready=f"{base_key}/ready.json",
    )


def build_ai_recovery_request_artifacts(
    *,
    bucket: str,
    analysis_id: str,
    pdf_bytes: bytes,
    page_count: int,
    deterministic_artifact: Mapping[str, object],
    source_evidence: Mapping[str, object],
    parser_release: str,
    layout_profile: str,
    layout_family: str,
    statement_type: str,
    layout_confidence: float,
    issue_codes: tuple[str, ...],
    ai: AIRecoveryVersionSet,
    created_at: datetime,
    expires_at: datetime,
    prefix: str = DEFAULT_AI_RECOVERY_REQUEST_PREFIX,
) -> AIRecoveryRequestArtifacts:
    _require_timezone(created_at, field_name="created_at")
    _require_timezone(expires_at, field_name="expires_at")
    normalized_bucket = str(bucket or "").strip()
    normalized_analysis_id = str(analysis_id or "").strip()
    normalized_parser_release = str(parser_release or "").strip()
    immutable_pdf = bytes(pdf_bytes)
    document_sha256 = hashlib.sha256(immutable_pdf).hexdigest()
    idempotency_key = build_ai_recovery_idempotency_key(
        analysis_id=normalized_analysis_id,
        document_sha256=document_sha256,
        parser_release=normalized_parser_release,
        prompt_version=ai.prompt_version,
        output_schema_version=ai.output_schema_version,
        comparator_version=ai.comparator_version,
        model_id=ai.model_id,
    )
    keys = build_ai_recovery_object_keys(
        prefix=prefix,
        created_at=created_at,
        idempotency_key=idempotency_key,
    )
    manifest = AIRecoveryRequestManifest(
        schema_version="ai_recovery_request_v1",
        idempotency_key=idempotency_key,
        analysis_id=normalized_analysis_id,
        document=AIRecoveryDocumentReference(
            bucket=normalized_bucket,
            key=keys.input_pdf,
            sha256=document_sha256,
            content_type="application/pdf",
            size_bytes=len(immutable_pdf),
            page_count=page_count,
        ),
        deterministic_artifact=DeterministicArtifactReference(
            key=keys.deterministic_artifact,
            source_evidence_key=keys.source_evidence,
            parser_release=normalized_parser_release,
            layout_profile=layout_profile,
            layout_family=layout_family,
            statement_type=statement_type,
            layout_confidence=layout_confidence,
            issue_codes=list(issue_codes),
        ),
        ai=ai,
        created_at=created_at,
        expires_at=expires_at,
    )
    return AIRecoveryRequestArtifacts(
        manifest=manifest,
        pdf_bytes=immutable_pdf,
        deterministic_artifact=_canonical_json_bytes(deterministic_artifact),
        source_evidence=_canonical_json_bytes(source_evidence),
    )


def validate_ai_recovery_request_artifacts(
    artifacts: AIRecoveryRequestArtifacts,
    *,
    bucket: str,
    prefix: str,
) -> AIRecoveryObjectKeys:
    manifest = artifacts.manifest
    if manifest.document.bucket != bucket:
        raise AIRecoveryRequestIntegrityError("AI recovery request bucket does not match the publisher bucket.")
    if len(artifacts.pdf_bytes) != manifest.document.size_bytes:
        raise AIRecoveryRequestIntegrityError("AI recovery PDF size does not match its manifest.")
    document_sha256 = hashlib.sha256(artifacts.pdf_bytes).hexdigest()
    if document_sha256 != manifest.document.sha256:
        raise AIRecoveryRequestIntegrityError("AI recovery PDF digest does not match its manifest.")

    expected_idempotency_key = build_ai_recovery_idempotency_key(
        analysis_id=manifest.analysis_id,
        document_sha256=manifest.document.sha256,
        parser_release=manifest.deterministic_artifact.parser_release,
        prompt_version=manifest.ai.prompt_version,
        output_schema_version=manifest.ai.output_schema_version,
        comparator_version=manifest.ai.comparator_version,
        model_id=manifest.ai.model_id,
    )
    if manifest.idempotency_key != expected_idempotency_key:
        raise AIRecoveryRequestIntegrityError("AI recovery idempotency key does not match its manifest inputs.")

    expected_keys = build_ai_recovery_object_keys(
        prefix=prefix,
        created_at=manifest.created_at,
        idempotency_key=manifest.idempotency_key,
    )
    referenced_keys = (
        manifest.document.key,
        manifest.deterministic_artifact.key,
        manifest.deterministic_artifact.source_evidence_key,
    )
    if referenced_keys != (
        expected_keys.input_pdf,
        expected_keys.deterministic_artifact,
        expected_keys.source_evidence,
    ):
        raise AIRecoveryRequestIntegrityError("AI recovery manifest object keys do not match the immutable key layout.")
    return expected_keys


def normalize_ai_recovery_request_prefix(prefix: str) -> str:
    normalized = str(prefix or "").strip().strip("/")
    segments = normalized.split("/")
    if (
        not normalized
        or "\\" in normalized
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("AI recovery S3 prefix is invalid.")
    return normalized


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_timezone(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information.")
