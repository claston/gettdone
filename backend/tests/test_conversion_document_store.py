from pathlib import Path
from typing import Any

import pytest

from app import dependencies
from app.application.conversion.conversion_document_store import (
    FilesystemConversionDocumentStore,
    S3ConversionDocumentStore,
)
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.dependencies import _build_conversion_document_store


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.last_body: _FakeS3Body | None = None

    def put_object(self, **kwargs) -> None:
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, **kwargs) -> dict[str, _FakeS3Body]:
        self.get_calls.append(kwargs)
        body = _FakeS3Body(self.objects[(kwargs["Bucket"], kwargs["Key"])])
        self.last_body = body
        return {"Body": body}

    def delete_object(self, **kwargs) -> None:
        self.delete_calls.append(kwargs)
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)


def test_filesystem_conversion_document_store_round_trips_without_exposing_path(tmp_path: Path) -> None:
    store = FilesystemConversionDocumentStore(root_dir=tmp_path / "jobs")
    document = ingest_uploaded_document("statement.csv", b"date,description,amount\n2026-01-01,PIX,10.00\n")

    reference = store.store(document)

    assert reference.filename == "statement.csv"
    assert reference.file_type == "csv"
    assert reference.storage_key.startswith("doc_")
    assert not hasattr(reference, "path")
    assert not hasattr(reference, "raw_bytes")
    loaded = store.load(reference)
    assert loaded.filename == document.filename
    assert loaded.raw_bytes == document.raw_bytes
    assert loaded.staging is not None
    assert loaded.staging.sha256_hex == reference.sha256_hex

    store.delete(reference)
    with pytest.raises(FileNotFoundError):
        store.load(reference)


def test_filesystem_conversion_document_store_rejects_invalid_storage_key(tmp_path: Path) -> None:
    store = FilesystemConversionDocumentStore(root_dir=tmp_path / "jobs")
    document = ingest_uploaded_document("statement.csv", b"date,description,amount\n")
    reference = store.store(document)
    invalid_reference = reference.__class__(
        storage_key="../escape",
        filename=reference.filename,
        file_type=reference.file_type,
        size_bytes=reference.size_bytes,
        sha256_hex=reference.sha256_hex,
    )

    with pytest.raises(ValueError, match="storage key"):
        store.load(invalid_reference)


def test_s3_conversion_document_store_round_trips_with_private_encrypted_object() -> None:
    client = _FakeS3Client()
    store = S3ConversionDocumentStore(
        bucket="private-conversion-bucket",
        prefix="conversion/jobs/",
        s3_client=client,
    )
    document = ingest_uploaded_document("statement.pdf", b"%PDF-1.7 synthetic statement")

    reference = store.store(document)

    assert reference.storage_key.startswith("doc_")
    assert client.put_calls == [
        {
            "Bucket": "private-conversion-bucket",
            "Key": f"conversion/jobs/{reference.storage_key}/document.pdf",
            "Body": document.raw_bytes,
            "ContentLength": document.size_bytes,
            "ContentType": "application/pdf",
            "Metadata": {"sha256": reference.sha256_hex},
            "ServerSideEncryption": "AES256",
        }
    ]

    loaded = store.load(reference)

    assert loaded.filename == document.filename
    assert loaded.raw_bytes == document.raw_bytes
    assert loaded.staging is None
    assert client.last_body is not None
    assert client.last_body.closed is True

    store.delete(reference)

    assert client.delete_calls == [
        {
            "Bucket": "private-conversion-bucket",
            "Key": f"conversion/jobs/{reference.storage_key}/document.pdf",
        }
    ]
    assert client.objects == {}


def test_s3_conversion_document_store_rejects_tampered_object() -> None:
    client = _FakeS3Client()
    store = S3ConversionDocumentStore(bucket="private-conversion-bucket", s3_client=client)
    reference = store.store(ingest_uploaded_document("statement.csv", b"date,amount\n2026-01-01,10\n"))
    object_key = ("private-conversion-bucket", f"conversion/jobs/{reference.storage_key}/document.csv")
    client.objects[object_key] = b"x" * reference.size_bytes

    with pytest.raises(ValueError, match="digest"):
        store.load(reference)


def test_s3_conversion_document_store_requires_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3ConversionDocumentStore(bucket="  ")


def test_conversion_document_store_config_defaults_to_filesystem(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CONVERSION_DOCUMENT_STORE", raising=False)

    store = _build_conversion_document_store(backend_root=tmp_path)

    assert isinstance(store, FilesystemConversionDocumentStore)
    assert store.root_dir == (tmp_path / "tmp" / "conversion_jobs" / "documents").resolve()


def test_conversion_document_store_config_builds_lazy_s3_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONVERSION_DOCUMENT_STORE", "s3")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "private-conversion-bucket")
    monkeypatch.setenv("CONVERSION_S3_PREFIX", "worker/input")
    monkeypatch.setenv("AWS_REGION", "sa-east-1")

    store = _build_conversion_document_store(backend_root=tmp_path)

    assert isinstance(store, S3ConversionDocumentStore)
    assert store.bucket == "private-conversion-bucket"
    assert store.prefix == "worker/input"
    assert store.region == "sa-east-1"
    assert store._s3_client is None


def test_conversion_document_store_config_rejects_unknown_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONVERSION_DOCUMENT_STORE", "shared-drive")

    with pytest.raises(RuntimeError, match="CONVERSION_DOCUMENT_STORE"):
        _build_conversion_document_store(backend_root=tmp_path)


def test_batch_document_store_uses_direct_upload_s3_even_when_legacy_store_is_local(monkeypatch, tmp_path: Path) -> None:
    local_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "legacy-documents")
    monkeypatch.setattr(dependencies, "_conversion_document_store", local_store)
    monkeypatch.setattr(dependencies, "_async_conversion_document_store", None, raising=False)
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "private-batch-bucket")
    monkeypatch.setenv("CONVERSION_S3_PREFIX", "conversion/jobs")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    store = dependencies.get_conversion_batch_document_store()

    assert isinstance(store, S3ConversionDocumentStore)
    assert store.bucket == "private-batch-bucket"
    assert store.prefix == "conversion/jobs"
    assert store.region == "us-east-1"
    assert store._s3_client is None
    assert dependencies.get_conversion_batch_document_store() is store
    assert dependencies.get_conversion_document_store() is local_store


def test_batch_document_store_reuses_configured_s3_store(monkeypatch) -> None:
    store = S3ConversionDocumentStore(bucket="private-batch-bucket")
    monkeypatch.setattr(dependencies, "_conversion_document_store", store)

    assert dependencies.get_conversion_batch_document_store() is store


def test_batch_document_store_preserves_local_setups_without_s3(monkeypatch, tmp_path: Path) -> None:
    store = FilesystemConversionDocumentStore(root_dir=tmp_path)
    monkeypatch.setattr(dependencies, "_conversion_document_store", store)
    monkeypatch.delenv("CONVERSION_S3_BUCKET", raising=False)

    assert dependencies.get_conversion_batch_document_store() is store
