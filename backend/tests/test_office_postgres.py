from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from test_canonical_movements_postgres import DSN, enroll
from test_canonical_pro import database as paid_database
from test_canonical_pro import enable

from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
from app.application.canonical_pro import CanonicalProService
from app.application.office_documents import OfficeReviews
from app.application.office_plan import office_commercial
from app.application.pro_organization import OrganizationConflict

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason='Requires disposable local PostgreSQL')


def test_office_migration_forward_rollback_keeps_legacy_jobs_and_new_metadata(database, monkeypatch):
    from alembic import command
    from alembic.config import Config
    repo, scope, _, schema, batches, _ = database
    assert batches.get(scope.job_id).job.product == 'free'
    reviews = OfficeReviews(repo.test_access.db)
    saved = reviews.save(scope.actor_id, scope.job_id, 'source', datetime.now(timezone.utc) + timedelta(hours=1),
                         'verified', None, 0, 'verification')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'alembic'))
    monkeypatch.setenv('DATABASE_URL', DSN)
    monkeypatch.setenv('DATABASE_SCHEMA', schema)
    command.downgrade(config, '20261118_02')
    assert batches.get(scope.job_id).job.product == 'free'
    assert reviews.read(scope.actor_id, scope.job_id, 'source') == saved
    command.upgrade(config, 'head')
    assert reviews.read(scope.actor_id, scope.job_id, 'source') == saved
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET product='pro' WHERE job_id=%s", (scope.job_id,))
    assert batches.get(scope.job_id).job.product == 'pro'


def test_office_verification_serializes_independent_connections(database):
    repo, scope, *_ = database
    def save(operation):
        try:
            return OfficeReviews(repo.test_access.db).save(scope.actor_id, scope.job_id, 'source',
                datetime.now(timezone.utc) + timedelta(hours=1), 'verified', None, 0, operation)
        except OrganizationConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, ('one', 'two')))
    assert results.count('conflict') == 1
    with repo._connect() as conn:
        assert conn.execute(f'SELECT count(*) AS n FROM {repo.p}pro_document_review_events').fetchone()['n'] == 1


def test_office_package_uses_canonical_edits_and_new_plan_expiration(database, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.routers import canonical_pro
    from app.routers.office_documents import PackageRequest, download_package, preview_package
    repo, scope, content, _, batches, organization = database
    content['document_type'] = 'bank_statement'
    initial = enroll(database, content)
    enable(database)
    canonical = CanonicalProService(repo, LocalCanonicalObjects(tmp_path / 'artifacts'))
    monkeypatch.setattr(canonical_pro, 'get_canonical_pro_service', lambda: canonical)
    canonical.edit(scope, [{'movement_id': initial['movement_ids']['stable'], 'credit': 25.50,
                            'date': '2026-04-01', 'description': 'Revisado'}],
        expected_version=1, operation_id='edit')
    saved = canonical.edit(scope, [], expected_version=2, operation_id='review', mark_reviewed=True)
    assert saved['reviewed_at']
    admin = dict(access=repo.test_access, actor_kind='system', actor_user_id=None)
    office_commercial(**admin, operation_id='configure', action='configure', values=dict(version=1,
        price_cents=1000, quota_limit=10, max_pages_per_file=100, max_pages_per_file_ocr=6, batch_max_files=12))
    office_commercial(**admin, operation_id='activate', action='activate', values=dict(user_id=scope.actor_id,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    request = PackageRequest(client_id=scope.client_id, job_ids=[scope.job_id])
    args = dict(owner=scope.actor_id, service=organization, batches=SimpleNamespace(repository=batches), report=None, async_report=None)
    preview = preview_package(request, **args)
    assert preview['items'][0]['bank_reviewed']
    request.snapshot = preview['snapshot']
    request.accept_partial = True
    store = SimpleNamespace(load=lambda _: SimpleNamespace(raw_bytes=b'date,amount\n2026-04-01,10\n'))
    response = download_package(request, **args, store=store, access=repo.test_access)
    with ZipFile(BytesIO(response.body)) as archive:
        ofx = next(name for name in archive.namelist() if name.endswith('.ofx'))
        assert b'25.50' in archive.read(ofx)
    canonical.edit(scope, [{'movement_id': initial['movement_ids']['stable'], 'credit': 30,
                            'date': '2026-04-01', 'description': 'Nova revisão'}],
        expected_version=saved['version'], operation_id='later')
    with pytest.raises(HTTPException) as stale:
        download_package(request, **args, store=store, access=repo.test_access)
    assert stale.value.status_code == 409
    office_commercial(**admin, operation_id='cancel', action='cancel', values=dict(user_id=scope.actor_id))
    from app.application.errors import AnalysisAccessDeniedError
    with pytest.raises(AnalysisAccessDeniedError):
        canonical.resolve(scope.actor_id, scope.analysis_id)
