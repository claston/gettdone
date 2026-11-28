from datetime import datetime, timedelta, timezone

from app.application.document_retention import retention_deadline

NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)


def subscription(*, code="essencial", status="active", end=None, start=None):
    return {"code": code, "status": status, "started_at": (start or NOW - timedelta(days=20)).isoformat(),
            "ended_at": end.isoformat() if end else None, "batch_conversion_enabled": True}


def test_paid_plan_preserves_documents_until_paid_end_plus_thirty_days():
    end = NOW + timedelta(days=10)
    assert retention_deadline([subscription(end=end)], now=NOW) == end + timedelta(days=30)


def test_active_plan_without_end_has_no_scheduled_deletion():
    assert retention_deadline([subscription()], now=NOW).year == 9999


def test_ended_subscription_uses_actual_end_not_last_access():
    end = NOW - timedelta(days=12)
    assert retention_deadline([subscription(status="ended", end=end)], now=NOW) == end + timedelta(days=30)


def test_renewal_cancels_scheduled_deletion():
    assert retention_deadline([subscription(status="ended", end=NOW - timedelta(days=2)),
                               subscription(start=NOW - timedelta(days=1))], now=NOW).year == 9999


def test_trial_free_and_future_subscription_do_not_grant_retention():
    assert retention_deadline([subscription(code="free"), subscription(code="trial"),
                               subscription(start=NOW + timedelta(days=1))], now=NOW) is None


def test_ended_subscription_without_end_cannot_grant_indefinite_retention():
    assert retention_deadline([subscription(status="ended")], now=NOW) is None


def test_private_office_plan_requires_a_finite_term():
    assert retention_deadline([subscription(code="escritorio_documentos")], now=NOW) is None


def test_expired_grace_keeps_the_past_deadline():
    end = NOW - timedelta(days=31)
    assert retention_deadline([subscription(end=end)], now=NOW) == end + timedelta(days=30)


def test_legacy_result_uses_current_job_deadline_and_database_failure_never_deletes(tmp_path):
    import json

    import pytest

    from app.application.storage_service import TempAnalysisStorage

    deadline = NOW + timedelta(days=30)
    storage = TempAnalysisStorage(tmp_path, now_provider=lambda: NOW,
                                  retention_resolver=lambda analysis, job: deadline)
    directory = tmp_path / "an_retained"
    directory.mkdir()
    metadata = directory / "analysis.json"
    metadata.write_text(json.dumps({"analysis_id": "an_retained", "expires_at": (NOW - timedelta(days=2)).isoformat()}))
    assert not storage._is_expired(directory)
    assert storage.read_canonical_candidate("an_retained")["expires_at"] == deadline.isoformat()
    def offline(*args):
        raise RuntimeError("Database unavailable")
    storage.retention_resolver = offline
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.read_canonical_candidate("an_retained")
    assert metadata.exists()
