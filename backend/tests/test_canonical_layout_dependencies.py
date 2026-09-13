import pytest

from app import dependencies
from app.adapters.conversion.canonical_layout_store import S3CanonicalLayoutStore


@pytest.fixture(autouse=True)
def _reset_canonical_capture_service():
    dependencies._canonical_layout_capture_service = None
    yield
    dependencies._canonical_layout_capture_service = None


def test_dependency_keeps_canonical_capture_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CANONICAL_LAYOUT_CAPTURE_ENABLED", raising=False)
    monkeypatch.delenv("CONVERSION_S3_BUCKET", raising=False)

    service = dependencies.get_canonical_layout_capture_service()

    assert service.enabled is False


def test_dependency_uses_existing_conversion_bucket_and_dedicated_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANONICAL_LAYOUT_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "gettdone-conversions")
    monkeypatch.setenv("CANONICAL_LAYOUT_CAPTURE_S3_PREFIX", "private/canonical/v1")
    monkeypatch.setenv("AWS_REGION", "sa-east-1")

    service = dependencies.get_canonical_layout_capture_service()

    assert service.enabled is True
    assert isinstance(service.store, S3CanonicalLayoutStore)
    assert service.store.bucket == "gettdone-conversions"
    assert service.store.prefix == "private/canonical/v1"
    assert service.store.region == "sa-east-1"
