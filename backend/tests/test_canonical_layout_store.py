from typing import Any

import pytest

from app.adapters.conversion.canonical_layout_store import (
    S3CanonicalLayoutStore,
    build_s3_canonical_layout_capture_service,
)
from app.application.conversion.canonical_layout_capture import (
    CanonicalLayoutArtifact,
    CanonicalLayoutCaptureService,
)


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs) -> None:
        self.put_calls.append(kwargs)


def _artifact(*, validated: bool = True) -> CanonicalLayoutArtifact:
    return CanonicalLayoutArtifact(
        capture_id="cap_0123456789abcdef01234567",
        pdf_bytes=b"%PDF canonical",
        manifest={"schema_version": "1", "privacy_validation_version": "1"},
        privacy_validated=validated,
    )


def test_s3_store_uploads_only_canonical_artifact_with_aes256() -> None:
    client = _FakeS3Client()
    store = S3CanonicalLayoutStore(
        bucket="private-conversions",
        prefix="conversion/canonical-layouts/candidates/v1/",
        region="sa-east-1",
        s3_client=client,
    )

    store.store(_artifact())

    assert [call["Key"] for call in client.put_calls] == [
        "conversion/canonical-layouts/candidates/v1/cap_0123456789abcdef01234567/canonical.pdf",
        "conversion/canonical-layouts/candidates/v1/cap_0123456789abcdef01234567/layout.json",
    ]
    assert all(call["Bucket"] == "private-conversions" for call in client.put_calls)
    assert all(call["ServerSideEncryption"] == "AES256" for call in client.put_calls)
    assert all("SSEKMSKeyId" not in call for call in client.put_calls)
    assert client.put_calls[0]["ContentType"] == "application/pdf"
    assert client.put_calls[1]["ContentType"] == "application/json"
    assert set(client.put_calls[0]["Metadata"]) == {"schema-version", "privacy-validation-version"}
    assert b"analysis_id" not in client.put_calls[1]["Body"]
    assert b"filename" not in client.put_calls[1]["Body"]


def test_s3_store_rejects_artifact_that_did_not_pass_privacy_validation() -> None:
    store = S3CanonicalLayoutStore(
        bucket="private-conversions",
        s3_client=_FakeS3Client(),
    )

    with pytest.raises(ValueError, match="privacy validation"):
        store.store(_artifact(validated=False))


def test_s3_store_rejects_missing_bucket_or_unsafe_capture_id() -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3CanonicalLayoutStore(bucket=" ")

    store = S3CanonicalLayoutStore(bucket="private-conversions", s3_client=_FakeS3Client())
    unsafe = CanonicalLayoutArtifact(
        capture_id="../source-user-id",
        pdf_bytes=b"%PDF canonical",
        manifest={"schema_version": "1"},
        privacy_validated=True,
    )
    with pytest.raises(ValueError, match="capture id"):
        store.store(unsafe)


def test_capture_factory_is_disabled_without_requiring_s3_configuration() -> None:
    service = build_s3_canonical_layout_capture_service(enabled=False, bucket="")

    assert isinstance(service, CanonicalLayoutCaptureService)
    assert service.enabled is False
    assert service.generator is None
    assert service.store is None


def test_capture_factory_builds_aes256_s3_service_when_enabled() -> None:
    service = build_s3_canonical_layout_capture_service(
        enabled=True,
        bucket="private-conversions",
        prefix="canonical/candidates",
        region="sa-east-1",
        max_pages=12,
        max_extracted_chars=120_000,
    )

    assert service.enabled is True
    assert service.generator is not None
    assert service.generator.max_pages == 12
    assert service.generator.max_extracted_chars == 120_000
    assert isinstance(service.store, S3CanonicalLayoutStore)
    assert service.store.bucket == "private-conversions"
    assert service.store.prefix == "canonical/candidates"
    assert service.store.region == "sa-east-1"
