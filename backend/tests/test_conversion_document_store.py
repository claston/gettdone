from pathlib import Path

import pytest

from app.application.conversion.conversion_document_store import FilesystemConversionDocumentStore
from app.application.conversion.uploaded_document import ingest_uploaded_document


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
