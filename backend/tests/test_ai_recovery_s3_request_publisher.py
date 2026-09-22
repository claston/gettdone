from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.adapters.ai_recovery.s3_request_publisher import S3AIRecoveryRequestPublisher
from app.application.ai_recovery.request_publishing import (
    AIRecoveryObjectCollisionError,
    AIRecoveryRequestIntegrityError,
    build_ai_recovery_idempotency_key,
    build_ai_recovery_request_artifacts,
)
from app.application.ai_recovery.schemas import AIRecoveryVersionSet


class _PreconditionFailed(Exception):
    def __init__(self) -> None:
        self.response = {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        }
        super().__init__("precondition failed")


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, str]] = []

    def put_object(self, **kwargs) -> None:
        self.put_calls.append(kwargs)
        object_id = (kwargs["Bucket"], kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and object_id in self.objects:
            raise _PreconditionFailed()
        self.objects[object_id] = dict(kwargs)

    def head_object(self, **kwargs) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        stored = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {
            "ContentLength": stored["ContentLength"],
            "ContentType": stored["ContentType"],
            "Metadata": stored["Metadata"],
            "ServerSideEncryption": stored.get("ServerSideEncryption"),
        }


def _versions() -> AIRecoveryVersionSet:
    return AIRecoveryVersionSet(
        model_id="us.amazon.nova-2-lite-v1:0",
        prompt_version="nova_bank_statement_v2",
        output_schema_version="nova_statement_v2",
        comparator_version="statement_comparator_v1",
    )


def _artifacts():
    created_at = datetime(2026, 9, 22, 1, 30, tzinfo=timezone(timedelta(hours=3)))
    return build_ai_recovery_request_artifacts(
        bucket="private-ai-recovery",
        analysis_id="an_9f94d351c367",
        pdf_bytes=b"%PDF-1.7 synthetic statement",
        page_count=2,
        deterministic_artifact={
            "schema_version": "deterministic_statement_v1",
            "transactions": [{"amount": "10.00", "description": "PIX"}],
        },
        source_evidence={
            "schema_version": "source_evidence_v1",
            "lines": [{"page": 1, "text": "02/09 PIX 10,00"}],
        },
        parser_release="a681d70",
        layout_profile="banco_inter_extrato_conta_corrente_saldo_transacao_v1",
        layout_family="banco_inter_conta_corrente_saldo_por_transacao",
        statement_type="conta_corrente_extrato",
        layout_confidence=0.98,
        issue_codes=("balance_consistency_failed",),
        ai=_versions(),
        created_at=created_at,
        expires_at=created_at + timedelta(days=1),
    )


def test_idempotency_key_is_stable_and_covers_document_and_runtime_versions() -> None:
    values = {
        "analysis_id": "an_9f94d351c367",
        "document_sha256": "a" * 64,
        "parser_release": "a681d70",
        "prompt_version": "nova_bank_statement_v2",
        "output_schema_version": "nova_statement_v2",
        "comparator_version": "statement_comparator_v1",
        "model_id": "us.amazon.nova-2-lite-v1:0",
    }

    first = build_ai_recovery_idempotency_key(**values)
    replay = build_ai_recovery_idempotency_key(**values)

    expected_payload = "ai-recovery:v1\0" + "\0".join(values.values())
    assert first == replay == hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    for field in values:
        changed = dict(values)
        changed[field] = f"{values[field]}-changed"
        assert build_ai_recovery_idempotency_key(**changed) != first


def test_request_builder_uses_utc_partition_and_content_addressed_references() -> None:
    artifacts = _artifacts()
    manifest = artifacts.manifest
    base_key = f"ai-recovery/requests/v1/2026-09-21/{manifest.idempotency_key}"

    assert manifest.document.bucket == "private-ai-recovery"
    assert manifest.document.key == f"{base_key}/input.pdf"
    assert manifest.document.sha256 == hashlib.sha256(artifacts.pdf_bytes).hexdigest()
    assert manifest.document.size_bytes == len(artifacts.pdf_bytes)
    assert manifest.deterministic_artifact.key == f"{base_key}/deterministic.json"
    assert manifest.deterministic_artifact.source_evidence_key == f"{base_key}/source-evidence.json"


def test_publisher_writes_ready_manifest_last_with_conditional_aes256_uploads() -> None:
    client = _FakeS3Client()
    artifacts = _artifacts()
    publisher = S3AIRecoveryRequestPublisher(bucket="private-ai-recovery", s3_client=client)

    publication = publisher.publish(artifacts)

    base_key = f"ai-recovery/requests/v1/2026-09-21/{artifacts.manifest.idempotency_key}"
    expected_keys = (
        f"{base_key}/input.pdf",
        f"{base_key}/deterministic.json",
        f"{base_key}/source-evidence.json",
        f"{base_key}/ready.json",
    )
    assert tuple(call["Key"] for call in client.put_calls) == expected_keys
    assert publication.ready_key == expected_keys[-1]
    assert publication.created_keys == expected_keys
    assert publication.reused_keys == ()
    assert all(call["Bucket"] == "private-ai-recovery" for call in client.put_calls)
    assert all(call["IfNoneMatch"] == "*" for call in client.put_calls)
    assert all(call["ServerSideEncryption"] == "AES256" for call in client.put_calls)
    assert all("SSEKMSKeyId" not in call for call in client.put_calls)
    assert all(call["Metadata"]["sha256"] == hashlib.sha256(call["Body"]).hexdigest() for call in client.put_calls)
    assert [call["ContentType"] for call in client.put_calls] == [
        "application/pdf",
        "application/json",
        "application/json",
        "application/json",
    ]
    assert json.loads(client.put_calls[-1]["Body"]) == artifacts.manifest.model_dump(mode="json")


def test_publisher_replay_reuses_identical_objects_without_overwriting() -> None:
    client = _FakeS3Client()
    artifacts = _artifacts()
    publisher = S3AIRecoveryRequestPublisher(bucket="private-ai-recovery", s3_client=client)

    first = publisher.publish(artifacts)
    original_objects = {key: dict(value) for key, value in client.objects.items()}
    replay = publisher.publish(artifacts)

    assert len(first.created_keys) == 4
    assert replay.created_keys == ()
    assert replay.reused_keys == first.created_keys
    assert client.objects == original_objects
    assert [call["Key"] for call in client.put_calls[-4:]][-1].endswith("/ready.json")
    assert len(client.head_calls) == 4


def test_publisher_rejects_an_existing_object_with_different_content() -> None:
    client = _FakeS3Client()
    artifacts = _artifacts()
    input_key = artifacts.manifest.document.key
    client.objects[("private-ai-recovery", input_key)] = {
        "Body": b"different PDF",
        "ContentLength": len(b"different PDF"),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": hashlib.sha256(b"different PDF").hexdigest()},
    }
    publisher = S3AIRecoveryRequestPublisher(bucket="private-ai-recovery", s3_client=client)

    with pytest.raises(AIRecoveryObjectCollisionError, match="existing S3 object"):
        publisher.publish(artifacts)

    assert not any(key.endswith("/ready.json") for _, key in client.objects)


@pytest.mark.parametrize("invalid_field", ["sha256", "size_bytes"])
def test_publisher_rejects_pdf_that_does_not_match_manifest(invalid_field: str) -> None:
    artifacts = _artifacts()
    invalid_value: object = "0" * 64 if invalid_field == "sha256" else len(artifacts.pdf_bytes) + 1
    invalid_document = artifacts.manifest.document.model_copy(update={invalid_field: invalid_value})
    invalid_manifest = artifacts.manifest.model_copy(update={"document": invalid_document})
    invalid_artifacts = artifacts.__class__(
        manifest=invalid_manifest,
        pdf_bytes=artifacts.pdf_bytes,
        deterministic_artifact=artifacts.deterministic_artifact,
        source_evidence=artifacts.source_evidence,
    )
    client = _FakeS3Client()
    publisher = S3AIRecoveryRequestPublisher(bucket="private-ai-recovery", s3_client=client)

    with pytest.raises(AIRecoveryRequestIntegrityError, match="PDF"):
        publisher.publish(invalid_artifacts)

    assert client.put_calls == []


def test_publisher_rejects_manifest_for_another_bucket_or_key_layout() -> None:
    artifacts = _artifacts()
    publisher = S3AIRecoveryRequestPublisher(bucket="another-private-bucket", s3_client=_FakeS3Client())

    with pytest.raises(AIRecoveryRequestIntegrityError, match="bucket"):
        publisher.publish(artifacts)

    invalid_document = artifacts.manifest.document.model_copy(update={"key": "unexpected/input.pdf"})
    invalid_manifest = artifacts.manifest.model_copy(update={"document": invalid_document})
    invalid_artifacts = artifacts.__class__(
        manifest=invalid_manifest,
        pdf_bytes=artifacts.pdf_bytes,
        deterministic_artifact=artifacts.deterministic_artifact,
        source_evidence=artifacts.source_evidence,
    )
    publisher = S3AIRecoveryRequestPublisher(bucket="private-ai-recovery", s3_client=_FakeS3Client())

    with pytest.raises(AIRecoveryRequestIntegrityError, match="object keys"):
        publisher.publish(invalid_artifacts)


def test_publisher_requires_bucket_and_rejects_unsafe_prefix() -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3AIRecoveryRequestPublisher(bucket=" ", s3_client=_FakeS3Client())
    with pytest.raises(ValueError, match="prefix"):
        S3AIRecoveryRequestPublisher(
            bucket="private-ai-recovery",
            prefix="ai-recovery/../other",
            s3_client=_FakeS3Client(),
        )


def test_request_builder_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone"):
        build_ai_recovery_request_artifacts(
            bucket="private-ai-recovery",
            analysis_id="an_9f94d351c367",
            pdf_bytes=b"%PDF synthetic",
            page_count=1,
            deterministic_artifact={"transactions": []},
            source_evidence={"lines": []},
            parser_release="a681d70",
            layout_profile="known_layout",
            layout_family="known_family",
            statement_type="checking_statement",
            layout_confidence=0.98,
            issue_codes=("balance_consistency_failed",),
            ai=_versions(),
            created_at=datetime(2026, 9, 22, 12, 0),
            expires_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        )
