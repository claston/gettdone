from datetime import datetime, timedelta, timezone

import pytest
from test_canonical_movements_postgres import DSN
from test_canonical_movements_postgres import database as canonical_database

pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


def test_paid_retention_propagates_and_renewal_does_not_resurrect_expired_documents(database):
    repo, scope, _, _, batches, _ = database
    original = batches.get(scope.job_id).expires_at
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code="essencial")
    # Existing Free conversions keep their original deadline.
    assert batches.get(scope.job_id).expires_at == original
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET product='pro',expires_at=expires_at WHERE job_id=%s", (scope.job_id,))
    assert batches.get(scope.job_id).expires_at.year == 9999
    end = datetime.now(timezone.utc)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}user_plan_subscriptions SET status='ended',ended_at=%s WHERE user_id=%s AND status='active'",
                     (end.isoformat(), scope.actor_id))
    assert batches.get(scope.job_id).expires_at == end + timedelta(days=30)
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code="essencial")
    assert batches.get(scope.job_id).expires_at.year == 9999
    # Simulate an entitlement whose grace period has already ended.
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}user_plan_subscriptions SET status='ended',ended_at=%s WHERE user_id=%s",
                     ((end - timedelta(days=31)).isoformat(), scope.actor_id))
    expired = batches.get(scope.job_id).expires_at
    assert expired < datetime.now(timezone.utc)
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code="essencial")
    assert batches.get(scope.job_id).expires_at == expired


def test_physical_cleanup_is_retryable_and_cannot_delete_active_documents(database):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.application.paid_document_cleanup import PaidDocumentCleanup
    repo, scope, _, _, batches, _ = database
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code='essencial')
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET product='pro',expires_at=expires_at WHERE job_id=%s", (scope.job_id,))
    original, result = Mock(), Mock()
    cleanup = PaidDocumentCleanup(repo, SimpleNamespace(delete=original), SimpleNamespace(delete_expired_pro_analysis=result))
    assert cleanup.run() == 0
    original.assert_not_called()
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}user_plan_subscriptions SET status='ended',ended_at=%s WHERE user_id=%s",
                     ((datetime.now(timezone.utc)-timedelta(days=31)).isoformat(), scope.actor_id))
    result.side_effect = ConnectionError('S3 unavailable')
    with pytest.raises(ConnectionError):
        cleanup.run()
    assert batches.get(scope.job_id).result is not None
    result.side_effect = None
    assert cleanup.run() == 1
    assert cleanup.run() == 0
    assert batches.get(scope.job_id).result is None
    assert batches.get(scope.job_id).status == 'expired'
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code='essencial')
    assert batches.get(scope.job_id).expires_at < datetime.now(timezone.utc)


def test_new_canonical_adoption_exposes_the_paid_lifetime_in_json_and_sql(database):
    from test_canonical_movements_postgres import enroll
    repo, scope, *_ = database
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code='essencial')
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET product='pro',expires_at=expires_at WHERE job_id=%s", (scope.job_id,))
    snapshot = enroll(database)
    assert datetime.fromisoformat(snapshot['expires_at']).year == 9999
    assert datetime.fromisoformat(repo.read(scope)['expires_at']).year == 9999


def test_result_resolver_handles_missing_job_and_registers_retry_artifacts(database):
    from app.adapters.conversion.document_retention import analysis_retention_resolver
    repo, scope, *_ = database
    repo.test_access.activate_user_plan(user_id=scope.actor_id, plan_code='essencial')
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET product='pro',expires_at=expires_at WHERE job_id=%s", (scope.job_id,))
    resolve = analysis_retention_resolver(DSN, repo.schema)
    assert resolve(scope.analysis_id).year == 9999
    assert resolve('pending', scope.job_id).year == 9999
    assert resolve('absent') is None
    resolve.register('retry-attempt', scope.job_id)
    resolve.register('retry-attempt', scope.job_id)
    with repo._connect() as conn:
        assert conn.execute(f"SELECT retained_analysis_ids FROM {repo.p}conversion_jobs WHERE job_id=%s",
                            (scope.job_id,)).fetchone()['retained_analysis_ids'] == ['retry-attempt']
