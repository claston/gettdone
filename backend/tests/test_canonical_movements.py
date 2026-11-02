"""Canonical contracts; PostgreSQL integration lives in the companion module."""
from decimal import Decimal

import pytest

from app.application.canonical_movement_schema_v1 import canonical_movement_schema_v1
from app.application.canonical_movements import CanonicalRollout, exact_amount


def test_rollout_requires_selected_new_paid_pro_document():
    rollout = CanonicalRollout(enabled=True, organization_ids=frozenset({"org"}))
    assert rollout.allows(organization_id="org", product="pro", new_analysis=True)
    for product in ("free", "trial", "unknown"):
        assert not rollout.allows(organization_id="org", product=product, new_analysis=True)
    assert not rollout.allows(organization_id="other", product="pro", new_analysis=True)
    assert not rollout.allows(organization_id="org", product="pro", new_analysis=False)
    assert not CanonicalRollout().allows(organization_id="org", product="pro", new_analysis=True)


def test_money_is_exact_and_never_silently_rounded():
    assert exact_amount("-123.45") == Decimal("-123.45")
    assert exact_amount(0) == Decimal("0.00")
    for value in ("NaN", "Infinity", "1.001", True, "10000000000000000.01"):
        with pytest.raises(ValueError):
            exact_amount(value)


def test_schema_rejects_identifiers_and_is_additive():
    with pytest.raises(ValueError):
        canonical_movement_schema_v1('public"; DROP SCHEMA public')
    ddl = "\n".join(canonical_movement_schema_v1("test"))
    assert "NUMERIC(18,2)" in ddl
    assert "DROP TABLE" not in ddl
    assert "ON DELETE CASCADE" in ddl


def test_s3_pending_fence_is_visible_to_an_independent_cache(tmp_path):
    from app.application.errors import AnalysisEditConflictError
    from app.application.s3_analysis_storage import S3AnalysisStorage
    from tests.test_s3_analysis_storage import FakeS3Client, _analysis

    client = FakeS3Client()
    first = S3AnalysisStorage(root_dir=tmp_path / "first", bucket="private", s3_client=client)
    first.save_analysis(_analysis())
    baseline = first.fence_canonical_analysis("an_shared123")
    second = S3AnalysisStorage(root_dir=tmp_path / "second", bucket="private", s3_client=client)
    assert second.fence_canonical_analysis("an_shared123") == baseline
    with pytest.raises(AnalysisEditConflictError):
        second.apply_convert_edits("an_shared123", [])
    with pytest.raises(AnalysisEditConflictError):
        second.get_convert_report_path("an_shared123", "csv")
